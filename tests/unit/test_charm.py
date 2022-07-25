# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import pytest
import yaml
from ops.model import ActiveStatus, WaitingStatus
from ops.testing import Harness

from charm import Operator


@pytest.fixture
def harness():
    return Harness(Operator)


@patch("charm.KubernetesServicePatch", lambda x, y: None)
def test_not_leader(harness):
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, WaitingStatus)
    assert ("status_set", "waiting", "Waiting for leadership", {"is_app": False}) \
           in harness._get_backend_calls()


def ensure_state(self):
    self.state.username = "test-username"
    self.state.password = "test-password"
    self.state.salt = b"$2b$12$T4A6H06j5ykPkY88iX0FMO"
    self.state.user_id = "123"


@patch("charm.KubernetesServicePatch", lambda x, y: None)
@patch("charm.Operator._update_layer")
def test_install_event(update, harness):
    harness.set_leader(True)
    harness.begin()

    harness.charm.on.install.emit()
    update.assert_called()
    assert ("status_set", "maintenance", "Configuring dex charm", {"is_app": False})\
           in harness._get_backend_calls()
    assert harness.get_container_pebble_plan("dex")._services is not None

    assert isinstance(harness.charm.model.unit.status, ActiveStatus)


@patch("charm.KubernetesServicePatch", lambda x, y: None)
@patch("charm.Operator._update_layer")
def test_config_changed(update, harness):
    harness.set_leader(True)
    harness.begin()

    harness.update_config({"static-username": "new-user"})

    update.assert_called()


@patch("charm.KubernetesServicePatch", lambda x, y: None)
@patch("charm.Operator._update_layer")
@patch.object(Operator, "ensure_state", ensure_state)
def test_main_oidc(update, harness):
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
    assert isinstance(harness.charm.model.unit.status, ActiveStatus)


@patch("charm.KubernetesServicePatch", lambda x, y: None)
@patch("charm.Operator._update_layer")
@patch.object(Operator, "ensure_state", ensure_state)
def test_main_ingress(update, harness):
    harness.set_leader(True)
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
        "prefix": "/dex",
        "rewrite": "/dex",
        "service": "dex-auth",
        "port": 5556,
    }

    assert data == yaml.safe_load(relation_data["data"])
