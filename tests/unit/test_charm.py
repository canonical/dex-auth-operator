# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from glob import glob
from pathlib import Path
from unittest.mock import call, patch

import pytest
import yaml
from ops.model import ActiveStatus, WaitingStatus
from ops.testing import Harness

from charm import Operator


@pytest.fixture
def harness():
    return Harness(Operator)


def test_not_leader(harness):
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, WaitingStatus)


def ensure_state(self):
    self.state.username = "test-username"
    self.state.password = "test-password"
    self.state.salt = b"$2b$12$T4A6H06j5ykPkY88iX0FMO"
    self.state.user_id = "123"


@patch("charm.codecs")
@patch("charm.Client")
@patch.object(Operator, "ensure_state", ensure_state)
def test_main_no_relation(mock_client, mock_codecs, harness):
    mock_codecs.load_all_yaml.return_value = [42]

    harness.set_leader(True)
    harness.begin_with_initial_hooks()

    # Ensure that manifests were loaded
    config_yaml = {
        "issuer": "/dex",
        "storage": {"type": "kubernetes", "config": {"inCluster": True}},
        "web": {"http": "0.0.0.0:5556"},
        "logger": {"level": "debug", "format": "text"},
        "oauth2": {"skipApprovalScreen": True},
        "staticClients": [],
        "connectors": None,
        "enablePasswordDB": True,
        "staticPasswords": [
            {
                "email": "test-username",
                "hash": "$2b$12$T4A6H06j5ykPkY88iX0FMOc0t6Q4nvx7kvcVQrdwY1hYCZK3LMx2O",
                "username": "test-username",
                "userID": "123",
            }
        ],
    }

    manifests = [Path(path).read_text() for path in glob("src/manifests/*.yaml")]
    context = {
        "name": "dex-auth",
        "namespace": None,
        "port": 5556,
        "config_yaml": json.dumps(config_yaml),
        "config_hash": "1419b40a8f223615121b8b1ffdb83a689c5da15e16b9b7fd71c192a544295995",
    }
    context = tuple(sorted(context.items()))
    expected = {(m, context) for m in manifests}
    calls = {
        (c.args[0], tuple(sorted(c.kwargs["context"].items())))
        for c in mock_codecs.load_all_yaml.call_args_list
    }
    assert calls == expected

    # And that they were created
    assert mock_client().create.call_args_list == [call(42)] * 10

    # And everything worked
    assert isinstance(harness.charm.model.unit.status, ActiveStatus)


@patch("charm.codecs")
@patch("charm.Client")
@patch.object(Operator, "ensure_state", ensure_state)
def test_main_oidc(mock_client, mock_codecs, harness):
    mock_codecs.load_all_yaml.return_value = [42]

    harness.set_leader(True)
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

    # Ensure that manifests were loaded
    config_yaml = {
        "issuer": "/dex",
        "storage": {"type": "kubernetes", "config": {"inCluster": True}},
        "web": {"http": "0.0.0.0:5556"},
        "logger": {"level": "debug", "format": "text"},
        "oauth2": {"skipApprovalScreen": True},
        "staticClients": [
            {"id": "id", "name": "name", "redirectURIs": ["uri1"], "secret": "secret"}
        ],
        "connectors": None,
        "enablePasswordDB": True,
        "staticPasswords": [
            {
                "email": "test-username",
                "hash": "$2b$12$T4A6H06j5ykPkY88iX0FMOc0t6Q4nvx7kvcVQrdwY1hYCZK3LMx2O",
                "username": "test-username",
                "userID": "123",
            }
        ],
    }

    manifests = [Path(path).read_text() for path in glob("src/manifests/*.yaml")]
    context = {
        "name": "dex-auth",
        "namespace": None,
        "port": 5556,
        "config_yaml": json.dumps(config_yaml),
        "config_hash": "2fc46e9ce15bc29e0c3c317f4de635f1295265d05c410e96843140369ee3bf50",
    }
    context = tuple(sorted(context.items()))
    expected = {(m, context) for m in manifests}
    calls = {
        (c.args[0], tuple(sorted(c.kwargs["context"].items())))
        for c in mock_codecs.load_all_yaml.call_args_list
    }
    assert calls == expected

    # And that they were created
    assert mock_client().create.call_args_list == [call(42)] * 20

    # And everything worked
    assert isinstance(harness.charm.model.unit.status, ActiveStatus)


@patch("charm.codecs")
@patch("charm.Client")
@patch.object(Operator, "ensure_state", ensure_state)
def test_main_ingress(mock_client, mock_codecs, harness):
    mock_codecs.load_all_yaml.return_value = [42]

    harness.set_leader(True)
    rel_id = harness.add_relation("ingress", "app")
    harness.add_relation_unit(rel_id, "app/0")
    harness.update_relation_data(
        rel_id,
        "app",
        {"_supported_versions": "- v1"},
    )
    harness.begin_with_initial_hooks()

    # Ensure that manifests were loaded
    config_yaml = {
        "issuer": "/dex",
        "storage": {"type": "kubernetes", "config": {"inCluster": True}},
        "web": {"http": "0.0.0.0:5556"},
        "logger": {"level": "debug", "format": "text"},
        "oauth2": {"skipApprovalScreen": True},
        "staticClients": [],
        "connectors": None,
        "enablePasswordDB": True,
        "staticPasswords": [
            {
                "email": "test-username",
                "hash": "$2b$12$T4A6H06j5ykPkY88iX0FMOc0t6Q4nvx7kvcVQrdwY1hYCZK3LMx2O",
                "username": "test-username",
                "userID": "123",
            }
        ],
    }

    manifests = [Path(path).read_text() for path in glob("src/manifests/*.yaml")]
    context = {
        "name": "dex-auth",
        "namespace": None,
        "port": 5556,
        "config_yaml": json.dumps(config_yaml),
        "config_hash": "1419b40a8f223615121b8b1ffdb83a689c5da15e16b9b7fd71c192a544295995",
    }
    context = tuple(sorted(context.items()))
    expected = {(m, context) for m in manifests}
    calls = {
        (c.args[0], tuple(sorted(c.kwargs["context"].items())))
        for c in mock_codecs.load_all_yaml.call_args_list
    }
    assert calls == expected

    # And that they were created
    assert mock_client().create.call_args_list == [call(42)] * 20

    # And everything worked
    assert isinstance(harness.charm.model.unit.status, ActiveStatus)
