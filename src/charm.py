#!/usr/bin/env python3

import logging
import random
import string
import subprocess
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import yaml

from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus, BlockedStatus
from ops.framework import StoredState

from oci_image import OCIImageResource, OCIImageResourceError
from serialized_data_interface import (
    NoCompatibleVersions,
    NoVersionsListed,
    get_interfaces,
)

try:
    import bcrypt
except ImportError:
    subprocess.check_call(["apt", "update"])
    subprocess.check_call(["apt", "install", "-y", "python3-bcrypt"])
    import bcrypt


class Operator(CharmBase):
    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            self.model.unit.status = WaitingStatus("Waiting for leadership")
            return
        self.log = logging.getLogger(__name__)
        self.image = OCIImageResource(self, "oci-image")
        try:
            self.interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            self.model.unit.status = WaitingStatus(str(err))
            return
        except NoCompatibleVersions as err:
            self.model.unit.status = BlockedStatus(str(err))
            return
        self._stored.set_default(username="admin")
        self._stored.set_default(
            password="".join(random.choices(string.ascii_letters, k=30))
        )
        generated_salt = bcrypt.gensalt()
        self._stored.set_default(salt=generated_salt)
        self._stored.set_default(user_id=str(uuid4()))

        for event in [
            self.on.install,
            self.on.leader_elected,
            self.on.upgrade_charm,
            self.on.config_changed,
            self.on.oidc_client_relation_changed,
        ]:
            self.framework.observe(event, self.main)

        self.framework.observe(self.on["ingress"].relation_changed, self.send_info)

    def send_info(self, event):
        if self.interfaces["ingress"]:
            self.interfaces["ingress"].send_data(
                {
                    "prefix": "/dex",
                    "rewrite": "/",
                    "service": self.model.app.name,
                    "port": self.model.config["port"],
                }
            )

    def main(self, event):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            self.model.unit.status = e.status
            self.log.info(e)
            return

        self.model.unit.status = MaintenanceStatus("Setting pod spec")

        connectors = yaml.safe_load(self.model.config["connectors"])
        port = self.model.config["port"]
        public_url = self.model.config["public-url"]

        if (oidc_client := self.interfaces["oidc-client"]) and oidc_client.get_data():
            oidc_client_info = list(oidc_client.get_data().values())
        else:
            oidc_client_info = []

        # Allows setting a basic username/password combo
        static_username = self.model.config["static-username"]
        static_password = self.model.config["static-password"]

        static_config = {}

        # Dex needs some way of logging in, so if nothing has been configured,
        # just generate a username/password
        if not static_username:
            static_username = self._stored.username

        if not static_password:
            static_password = self._stored.password

        salt = self._stored.salt
        user_id = self._stored.user_id

        hashed = bcrypt.hashpw(static_password.encode("utf-8"), salt).decode("utf-8")
        static_config = {
            "enablePasswordDB": True,
            "staticPasswords": [
                {
                    "email": static_username,
                    "hash": hashed,
                    "username": static_username,
                    "userID": user_id,
                }
            ],
        }

        config = yaml.dump(
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

        self.model.pod.set_spec(
            {
                "version": 3,
                "serviceAccount": {
                    "roles": [
                        {
                            "global": True,
                            "rules": [
                                {
                                    "apiGroups": ["dex.coreos.com"],
                                    "resources": ["*"],
                                    "verbs": ["*"],
                                },
                                {
                                    "apiGroups": ["apiextensions.k8s.io"],
                                    "resources": ["customresourcedefinitions"],
                                    "verbs": ["create"],
                                },
                            ],
                        },
                    ],
                },
                "containers": [
                    {
                        "name": "dex-auth",
                        "imageDetails": image_details,
                        "command": ["dex", "serve", "/etc/dex/cfg/config.yaml"],
                        "ports": [{"name": "http", "containerPort": port}],
                        "envConfig": {"CONFIG_HASH": config_hash.hexdigest()},
                        "volumeConfig": [
                            {
                                "name": "config",
                                "mountPath": "/etc/dex/cfg",
                                "files": [
                                    {
                                        "path": "config.yaml",
                                        "content": config,
                                    },
                                ],
                            },
                        ],
                    }
                ],
                "kubernetesResources": {
                    "customResourceDefinitions": [
                        {"name": crd["metadata"]["name"], "spec": crd["spec"]}
                        for crd in yaml.safe_load_all(
                            Path("resources/crds.yaml").read_text()
                        )
                    ],
                },
            }
        )
        self.model.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(Operator)
