# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness
from unittest.mock import patch
import yaml

from charm import Operator


@pytest.fixture
def harness():
    return Harness(Operator)


def test_not_leader(harness):
    harness.begin()
    assert isinstance(harness.charm.model.unit.status, WaitingStatus)


def test_missing_image(harness):
    harness.set_leader(True)
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, BlockedStatus)


@patch("bcrypt.hashpw")
def test_main_no_relation(mock_pw, harness):
    mock_pw.return_value = "".encode("utf-8")
    harness.set_leader(True)
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "quay.io/dexidp/dex:v2.22.0",
            "username": "",
            "password": "",
        },
    )
    harness.begin_with_initial_hooks()
    pod_spec = harness.get_pod_spec()

    # confirm that we can serialize the pod spec
    yaml.safe_dump(pod_spec)

    assert isinstance(harness.charm.model.unit.status, ActiveStatus)


@patch("bcrypt.hashpw")
def test_main_oidc(mock_pw, harness):
    mock_pw.return_value = "".encode("utf-8")
    harness.set_leader(True)
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "quay.io/dexidp/dex:v2.22.0",
            "username": "",
            "password": "",
        },
    )
    rel_id = harness.add_relation("oidc-client", "app")

    harness.add_relation_unit(rel_id, "app/0")
    data = {
        "id": "id",
        "name": "name",
        "redirectURIs": ["uri1"],
        "secret": "secret",
    }
    harness.update_relation_data(
        rel_id,
        "app",
        {"_supported_versions": "- v1", "data": yaml.dump(data)},
    )
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, ActiveStatus)
    pod_spec = harness.get_pod_spec()
    config_yaml = pod_spec[0]["containers"][0]["volumeConfig"][0]["files"][0]["content"]

    assert data == yaml.safe_load(config_yaml)["staticClients"][0]


@patch("bcrypt.hashpw")
def test_main_ingress(mock_pw, harness):
    mock_pw.return_value = "".encode("utf-8")
    harness.set_leader(True)
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "quay.io/dexidp/dex:v2.22.0",
            "username": "",
            "password": "",
        },
    )
    rel_id = harness.add_relation("ingress", "app")
    harness.add_relation_unit(rel_id, "app/0")
    harness.update_relation_data(
        rel_id,
        "app",
        {"_supported_versions": "- v1"},
    )
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, ActiveStatus)
    relation_data = harness.get_relation_data(rel_id, harness.charm.app.name)
    data = {
        "port": 5556,
        "rewrite": "/dex",
        "prefix": "/dex",
        "service": "dex-auth",
    }

    assert data == yaml.safe_load(relation_data["data"])
