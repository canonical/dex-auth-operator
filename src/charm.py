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
from lightkube.resources.apps_v1 import StatefulSet, Deployment
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from serialized_data_interface import get_interface
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_delay,
    wait_exponential,
)


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

        self.logger: logging.Logger = logging.getLogger(__name__)

        for event in [
            self.on.install,
            self.on.upgrade_charm,
            self.on.config_changed,
            self.on.oidc_client_relation_changed,
            self.on.ingress_relation_changed,
        ]:
            self.framework.observe(event, self.main)

        self._max_time_checking_resources = 150

        self._workload_service_name = self.model.app.name + "-workload"

    @only_leader
    def main(self, event):
        self.model.unit.status = MaintenanceStatus("Calculating manifests")
        self.ensure_state()

        try:
            manifest = self.get_manifest()
        except Exception as err:
            self.model.unit.status = BlockedStatus(str(err))
            return

        self.model.unit.status = MaintenanceStatus("Applying manifests")
        errors = self.set_manifest(manifest)

        if errors:
            self.model.unit.status = BlockedStatus(
                f"There were {len(errors)} errors while applying manifests."
            )
            for error in errors:
                self.logger.error(error)
        else:
            # Ensure requested resources are up
            try:
                for attempt in Retrying(
                    retry=retry_if_exception_type(CheckFailed),
                    stop=stop_after_delay(max_delay=self._max_time_checking_resources),
                    wait=wait_exponential(multiplier=0.1, min=0.1, max=15),
                    reraise=True,
                ):
                    with attempt:
                        self.logger.info(
                            f"Checking status of requested resources (attempt "
                            f"{attempt.retry_state.attempt_number})"
                        )
                        self._check_deployed_resources()
            except CheckFailed:
                self.unit.status = BlockedStatus(
                    "Some Kubernetes resources did not start correctly during install"
                )
                return

            # Otherwise, application is working as expected
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
                    "service": self._workload_service_name,
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

    def _check_deployed_resources(self, manifest=None):
        """Check the status of deployed resources, returning True if ok else raising CheckFailed

        All abnormalities are captured in logs

        Params:
          manifest: (Optional) list of lightkube objects describing the entire application.  If
                    omitted, will be computed using self.get_manifest()
        """
        if manifest:
            expected_resources = manifest
        else:
            expected_resources = self.get_manifest()
        found_resources = [None] * len(expected_resources)
        errors = []

        client = Client()

        self.logger.info("Checking for expected resources")
        for i, resource in enumerate(expected_resources):
            try:
                found_resources[i] = client.get(
                    type(resource),
                    resource.metadata.name,
                    namespace=resource.metadata.namespace,
                )
            except ApiError:
                errors.append(
                    f"Cannot find k8s object for metadata '{resource.metadata}'"
                )

        self.logger.info("Checking readiness of found StatefulSets/Deployments")
        statefulsets_ok, statefulsets_errors = validate_statefulsets_and_deployments(
            found_resources
        )
        errors.extend(statefulsets_errors)

        # Log any errors
        for err in errors:
            self.logger.info(err)

        if len(errors) == 0:
            return True
        else:
            raise CheckFailed(
                "Some Kubernetes resources missing/not ready.  See logs for details",
                WaitingStatus,
            )

    @staticmethod
    def set_manifest(manifest):
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

    @staticmethod
    def remove_manifest(manifest):
        client = Client()

        for resource in manifest:
            client.delete(type(resource), resource.metadata.name)


def validate_statefulsets_and_deployments(objs):
    """Determines if all StatefulSets/Deployments have the expected number of readyReplicas

    Returns: Tuple of (Success [Boolean], Errors [list of str error messages]
    """
    errors = []

    for obj in objs:
        if isinstance(obj, (StatefulSet, Deployment)):
            readyReplicas = obj.status.readyReplicas
            replicas_expected = obj.spec.replicas
            if readyReplicas != replicas_expected:
                message = (
                    f"StatefulSet {obj.metadata.name} in namespace "
                    f"{obj.metadata.namespace} has {readyReplicas} readyReplicas, "
                    f"expected {replicas_expected}"
                )
                errors.append(message)

    return len(errors) == 0, errors


class CheckFailed(Exception):
    """Raise this exception if one of the checks in main fails."""

    def __init__(self, msg, status_type=None):
        super().__init__()

        self.msg = str(msg)
        self.status_type = status_type
        self.status = status_type(msg)


if __name__ == "__main__":
    main(Operator)
