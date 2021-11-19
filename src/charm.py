#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import subprocess
from functools import wraps
from glob import glob
from hashlib import sha256
from pathlib import Path
from random import choices
from string import ascii_letters
from uuid import uuid4

import yaml
from lightkube import ApiError, Client, codecs
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from serialized_data_interface import get_interface

try:
    import bcrypt
except ImportError:
    subprocess.check_call(["apt", "update"])
    subprocess.check_call(["apt", "install", "-y", "python3-bcrypt"])
    import bcrypt


def only_leader(handler):
    """Ensures method only runs if unit is a leader."""

    @wraps(handler)
    def wrapper(self, event):
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            self.model.unit.status = WaitingStatus("Waiting for leadership")
        else:
            handler(self, event)

    return wrapper


class Operator(CharmBase):
    state = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        for event in [
            self.on.install,
            self.on.upgrade_charm,
            self.on.config_changed,
            self.on.oidc_client_relation_changed,
            self.on.ingress_relation_changed,
        ]:
            self.framework.observe(event, self.main)

    @only_leader
    def main(self, event):
        self.model.unit.status = MaintenanceStatus("Calculating manifests")
        self.ensure_state()

        try:
            manifest = self.get_manifest()
        except Exception as err:
            raise
            self.model.unit.status = BlockedStatus(str(err))
            return

        self.model.unit.status = MaintenanceStatus("Applying manifests")
        errors = self.set_manifest(manifest)

        if errors:
            self.model.unit.status = BlockedStatus(
                f"There were {len(errors)} errors while applying manifests."
            )
            log = logging.getLogger(__name__)
            for error in errors:
                log.error(error)
        else:
            self.model.unit.status = ActiveStatus()

    @only_leader
    def remove(self, event):
        """Remove charm."""

        self.model.unit.status = MaintenanceStatus("Calculating manifests")
        self.ensure_state()

        manifest = self.get_manifest()

        self.model.unit.status = MaintenanceStatus("Removing manifests")

        self.remove_manifest(manifest)

    def ensure_state(self):
        self.state.set_default(
            username="admin",
            password="".join(choices(ascii_letters, k=30)),
            salt=bcrypt.gensalt(),
            user_id=str(uuid4()),
        )

    def get_manifest(self):
        # Handle ingress
        ingress = get_interface(self, "ingress")
        if ingress:
            for app_name, version in ingress.versions.items():
                data = {
                    "prefix": "/dex",
                    "rewrite": "/dex",
                    "service": self.model.app.name,
                    "port": self.model.config["port"],
                }

                ingress.send_data(data, app_name)

        # Get OIDC client info
        oidc = get_interface(self, "oidc-client")
        if oidc:
            oidc_client_info = list(oidc.get_data().values())
        else:
            oidc_client_info = []

        # Load config values as convenient variables
        connectors = yaml.safe_load(self.model.config["connectors"])
        port = self.model.config["port"]
        public_url = self.model.config["public-url"]
        static_username = self.model.config["static-username"] or self.state.username
        static_password = self.model.config["static-password"] or self.state.password
        static_password = static_password.encode("utf-8")
        hashed = bcrypt.hashpw(static_password, self.state.salt).decode("utf-8")

        static_config = {
            "enablePasswordDB": True,
            "staticPasswords": [
                {
                    "email": static_username,
                    "hash": hashed,
                    "username": static_username,
                    "userID": self.state.user_id,
                }
            ],
        }

        config = json.dumps(
            {
                "issuer": f"{public_url}/dex",
                "storage": {"type": "kubernetes", "config": {"inCluster": True}},
                "web": {"http": f"0.0.0.0:{port}"},
                "logger": {"level": "debug", "format": "text"},
                "oauth2": {"skipApprovalScreen": True},
                "staticClients": oidc_client_info,
                "connectors": connectors,
                **static_config,
            }
        )

        # Kubernetes won't automatically restart the pod when the configmap changes
        # unless we manually add the hash somewhere into the Deployment spec, so that
        # it changes whenever the configmap changes.
        config_hash = sha256()
        config_hash.update(config.encode("utf-8"))

        context = {
            "name": self.model.app.name.replace("-operator", ""),
            "namespace": self.model.name,
            "port": self.model.config["port"],
            "config_yaml": config,
            "config_hash": config_hash.hexdigest(),
        }

        return [
            obj
            for path in glob("src/manifests/*.yaml")
            for obj in codecs.load_all_yaml(Path(path).read_text(), context=context)
        ]

    def set_manifest(self, manifest):
        client = Client()
        errors = []

        for resource in manifest:
            try:
                client.create(resource)
            except ApiError as err:
                if err.status.reason == "AlreadyExists":
                    client.patch(type(resource), resource.metadata.name, resource)
                else:
                    errors.append(err)

        return errors

    def remove_manifest(self, manifest):
        client = Client()

        for resource in manifest:
            client.delete(type(resource), resource.metadata.name)


if __name__ == "__main__":
    main(Operator)
