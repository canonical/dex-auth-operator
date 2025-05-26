#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import secrets
import typing
from random import choices
from string import ascii_letters
from uuid import uuid4

import bcrypt
import yaml
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from charms.dex_auth.v0.dex_oidc_config import DexOidcConfigProvider
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.hydra.v0.oauth import (
    ClientChangedEvent,
    ClientCreatedEvent,
    ClientDeletedEvent,
    OAuthProvider,
)
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer
from lightkube.models.core_v1 import ServicePort
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    SecretNotFoundError,
    WaitingStatus,
)
from ops.pebble import Layer
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed, get_interface

METRICS_PATH = "/metrics"
METRICS_PORT = "5558"

INGRESS_V2_INTEGRATION_NAME = "ingress-v2"
OAUTH_INTEGRATION_NAME = "oauth"
DEFAULT_OAUTH_SCOPES = ["openid", "email", "profile"]

OAUTH_STATIC_CLIENT_SECRET_LABEL = "oauth.static.client"


class Operator(CharmBase):
    state = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        self.logger: logging.Logger = logging.getLogger(__name__)
        self._namespace = self.model.name

        self._ingress = IngressPerAppRequirer(
            self, relation_name=INGRESS_V2_INTEGRATION_NAME, port=int(self.model.config["port"])
        )
        # Patch the service to correctly expose the ports to be used
        dex_port = ServicePort(int(self.model.config["port"]), name="dex")
        metrics_port = ServicePort(int(METRICS_PORT), name="metrics-port")

        # Instantiate a DexOidcConfigProvider to share this app's issuer_url
        self.dex_oidc_config_provider = DexOidcConfigProvider(
            self, issuer_url=self._issuer_url, refresh_events=[self.on.config_changed]
        )

        self._oauth_provider = OAuthProvider(self, relation_name=OAUTH_INTEGRATION_NAME)

        self.service_patcher = KubernetesServicePatch(
            self,
            [dex_port, metrics_port],
            service_name=f"{self.model.app.name}",
        )

        self.prometheus_provider = MetricsEndpointProvider(
            charm=self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": METRICS_PATH,
                    "static_configs": [{"targets": ["*:{}".format(METRICS_PORT)]}],
                }
            ],
        )
        self.dashboard_provider = GrafanaDashboardProvider(self)
        self._logging = LogForwarder(charm=self)

        self._container_name = "dex"
        self._container = self.unit.get_container(self._container_name)
        self._entrypoint = "/usr/local/bin/docker-entrypoint"
        self._dex_config_path = "/etc/dex/config.docker.yaml"

        for event in [
            self.on.install,
            self.on.leader_elected,
            self.on.upgrade_charm,
            self.on.config_changed,
            self.on.oidc_client_relation_changed,
            self.on.ingress_relation_changed,
            self.on.dex_pebble_ready,
            self._ingress.on.ready,
            self._ingress.on.revoked,
            self.on.oauth_relation_created,
        ]:
            self.framework.observe(event, self.main)

        self.framework.observe(
            self._oauth_provider.on.client_created, self._on_oauth_client_created
        )
        self.framework.observe(
            self._oauth_provider.on.client_changed, self._on_oauth_client_changed
        )
        self.framework.observe(
            self._oauth_provider.on.client_deleted, self._on_oauth_client_deleted
        )

    @property
    def _dex_auth_layer(self) -> Layer:
        """Returns a pre-configured Pebble layer."""

        layer_config = {
            "summary": "dex-auth-operator layer",
            "description": "pebble config layer for dex-auth-operator",
            "services": {
                self._container_name: {
                    "override": "replace",
                    "summary": "entrypoint of the dex-auth-operator image",
                    "command": f"{self._entrypoint} dex serve {self._dex_config_path}",
                    "startup": "enabled",
                    "user": "_daemon_",
                    "environment": {
                        "KUBERNETES_POD_NAMESPACE": self._namespace,
                    },
                }
            },
        }
        return Layer(layer_config)

    @property
    def _issuer_url(self) -> str:
        """Return issuer-url value if config option exists; otherwise default Dex's endpoint."""
        if issuer_url := self.model.config["issuer-url"]:
            return issuer_url
        # TODO: remove this after the release of dex-auth 2.39
        # This should not be supported anymore, but has been kept for compatibility
        if public_url := self.model.config["public-url"]:
            self.logger.warning(
                "DEPRECATED: The public-url config option is deprecated"
                "Please leave empty or use issuer-url;"
                "otherwise this may cause unexpected behaviour."
            )
            # This will just cover cases like when the URL is my-dex.io:5557,
            # not when it starts with a different protocol
            if not public_url.startswith(("http://", "https://")):
                public_url = f"http://{public_url}"
            return f"{public_url}/dex"
        if ingress_url := self._ingress.url:
            return ingress_url
        return (
            f"http://{self.model.app.name}.{self._namespace}.svc:{self.model.config['port']}/dex"
        )

    def _update_layer(self, dex_oauth_static_client: typing.Optional[dict]) -> None:
        """Updates the Pebble configuration layer if changed."""
        self._check_container_connection()

        # Generate dex-auth configuration to be passed to the pebble layer
        # so the service is (re)started.
        dex_auth_config = self._generate_dex_auth_config(dex_oauth_static_client)

        # Get current layer
        current_layer = self._container.get_plan()
        # Create a new config layer
        new_layer = self._dex_auth_layer
        if current_layer.services != new_layer.services:
            self.unit.status = MaintenanceStatus("Applying new pebble layer")
            self._container.add_layer(self._container_name, new_layer, combine=True)
            self.logger.info("Pebble plan updated with new configuration")

        # Get current dex config
        current_config = self._container.pull(self._dex_config_path).read()
        if current_config != dex_auth_config:
            self._container.push(self._dex_config_path, dex_auth_config, make_dirs=True)
            self.logger.info("Updated dex config")

        # Using restart due to https://github.com/canonical/dex-auth-operator/issues/63
        self._container.restart(self._container_name)

    def ensure_state(self):
        self.state.set_default(
            username="admin",
            password="".join(choices(ascii_letters, k=30)),
            salt=bcrypt.gensalt(),
            user_id=str(uuid4()),
        )

    def handle_ingress(self):
        interface = self._get_interface("ingress")

        if not interface:
            return

        data = {
            "prefix": "/dex",
            "rewrite": "/dex",
            "service": self.model.app.name,
            "port": self.model.config["port"],
        }

        interface.send_data(data)

    def _check_leader(self):
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            raise ErrorWithStatus("Waiting for leadership", WaitingStatus)

    def _check_container_connection(self):
        if not self._container.can_connect():
            raise ErrorWithStatus("Waiting for pod startup to complete", WaitingStatus)

    def _get_interface(self, interface_name):
        try:
            interface = get_interface(self, interface_name)
        except NoVersionsListed as err:
            raise ErrorWithStatus(str(err), WaitingStatus)
        except NoCompatibleVersions as err:
            raise ErrorWithStatus(str(err), BlockedStatus)

        return interface

    def main(self, event):
        """Main reconcile loop.

        Args:
            event: Charm event
        """
        try:
            secret = self.model.get_secret(label=OAUTH_STATIC_CLIENT_SECRET_LABEL)
            oauth_client_secret = secret.get_content()
            dex_oauth_static_client = {
                "id": oauth_client_secret["client-id"],
                "secret": oauth_client_secret["client-secret"],
                "redirectURIs": [oauth_client_secret["redirect-uri"]],
                "name": oauth_client_secret["name"],
            }
        except SecretNotFoundError:
            dex_oauth_static_client = None

        try:
            self._check_leader()
            self.model.unit.status = MaintenanceStatus("Configuring dex charm")
            self.ensure_state()
            self._update_layer(dex_oauth_static_client)
            self.handle_ingress()
        except ErrorWithStatus as err:
            self.model.unit.status = err.status
            self.logger.error(f"Failed to handle {event} with error: {err}")
            return

        if self.unit.is_leader():
            self._oauth_provider.set_provider_info_in_relation_data(
                issuer_url=self._issuer_url,
                authorization_endpoint=f"{self._issuer_url}/auth",
                token_endpoint=f"{self._issuer_url}/token",
                jwks_endpoint=f"{self._issuer_url}/keys",
                introspection_endpoint=f"{self._issuer_url}/token/introspect",
                userinfo_endpoint=f"{self._issuer_url}/userinfo",
                scope=" ".join(DEFAULT_OAUTH_SCOPES),
            )

        self.model.unit.status = ActiveStatus()

    def _generate_dex_auth_config(self, dex_oauth_static_client: typing.Optional[dict]) -> str:
        """Returns dex-auth configuration to be passed when (re)starting the dex-auth service.

        Raises:
            CheckFailedError: when static login is disabled and no connectors are configured.
        """
        # Get OIDC client info
        oidc_client_info = []
        oidc = self._get_interface("oidc-client")
        if oidc:
            oidc_client_info.extend(list(oidc.get_data().values()))

        if dex_oauth_static_client:
            oidc_client_info.append(dex_oauth_static_client)

        # Load config values as convenient variables
        connectors = yaml.safe_load(self.model.config["connectors"])
        port = self.model.config["port"]

        enable_password_db = self.model.config["enable-password-db"]
        static_config = {
            "staticPasswords": [],
        }

        # The dex-auth service cannot be started correctly when the static
        # login is disabled, but no connector configuration is provided.
        if not enable_password_db and not connectors:
            raise ErrorWithStatus(
                "Please add a connectors configuration to proceed without a static login.",
                BlockedStatus,
            )

        if enable_password_db:
            static_username = self.model.config["static-username"] or self.state.username
            static_password = self.model.config["static-password"] or self.state.password
            static_password = static_password.encode("utf-8")
            hashed = bcrypt.hashpw(static_password, self.state.salt).decode("utf-8")
            static_config = {
                "staticPasswords": [
                    {
                        "email": static_username,
                        "hash": hashed,
                        "username": static_username,
                        "userID": self.state.user_id,
                    }
                ],
            }

        config = yaml.dump(
            {
                "issuer": self._issuer_url,
                "storage": {"type": "kubernetes", "config": {"inCluster": True}},
                "web": {"http": f"0.0.0.0:{port}"},
                "telemetry": {"http": "0.0.0.0:5558"},
                "logger": {"level": "debug", "format": "text"},
                "oauth2": {"skipApprovalScreen": True},
                "staticClients": oidc_client_info,
                "connectors": connectors,
                "enablePasswordDB": enable_password_db,
                **static_config,
            }
        )

        return config

    def _on_oauth_client_created(self, event: ClientCreatedEvent) -> None:
        """Handle oauth-client-created event.

        Args:
            event: ClientCreatedEvent
        """
        if self.unit.is_leader():
            oauth_client_static_secret = {
                "client-id": secrets.token_hex(16),
                "client-secret": secrets.token_hex(16),
                "redirect-uri": event.redirect_uri,
                "name": f"oauth_{event.relation_id}",
            }

            self.app.add_secret(
                content=oauth_client_static_secret, label=OAUTH_STATIC_CLIENT_SECRET_LABEL
            )

            self._oauth_provider.set_client_credentials_in_relation_data(
                event.relation_id,
                oauth_client_static_secret["client-id"],
                oauth_client_static_secret["client-secret"],
            )
            self.main(event)

    def _on_oauth_client_changed(self, event: ClientChangedEvent) -> None:
        """Handle oauth-client-changed event.

        Args:
            event: ClientChangedEvent
        """
        secret = self.model.get_secret(label=OAUTH_STATIC_CLIENT_SECRET_LABEL)

        secret.set_content({"redirectURIs": [event.redirect_uri]})
        self.main(event)

    def _on_oauth_client_deleted(self, event: ClientDeletedEvent) -> None:
        """Handle oauth-client-deleted event.

        Args:
            event: ClientDeletedEvent
        """
        if self.unit.is_leader():
            try:
                secret = self.model.get_secret(label=OAUTH_STATIC_CLIENT_SECRET_LABEL)
                secret.remove_all_revisions()
            except SecretNotFoundError:
                self.logger.warning("Secret not found, skipping deletion.")
        self.main(event)


if __name__ == "__main__":
    main(Operator)
