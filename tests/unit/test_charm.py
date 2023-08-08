# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import ops
import pytest
import yaml
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

from charm import CheckFailedError, Operator

# SIMULATE_CAN_CONNECT is needed when using ops<2
ops.testing.SIMULATE_CAN_CONNECT = True


@pytest.fixture
def harness():
    return Harness(Operator)


@patch("charm.KubernetesServicePatch", lambda x, y: None)
def test_not_leader(harness):
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, WaitingStatus)
    assert (
        "status_set",
        "waiting",
        "Waiting for leadership",
        {"is_app": False},
    ) in harness._get_backend_calls()


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
    harness.set_can_connect("dex", True)

    harness.charm.on.install.emit()
    update.assert_called()
    assert (
        "status_set",
        "maintenance",
        "Configuring dex charm",
        {"is_app": False},
    ) in harness._get_backend_calls()
    assert harness.get_container_pebble_plan("dex")._services is not None

    assert isinstance(harness.charm.model.unit.status, ActiveStatus)


@patch("charm.KubernetesServicePatch", lambda x, y: None)
def test_generate_dex_auth_config_raises(harness):
    """Check the method raises when static login is disabled and no connectors are provided."""
    harness.begin()
    config_updates = {
        "enable-password-db": False,
        "port": 5555,
        "public-url": "dummy.url",
    }

    harness.update_config(config_updates)

    with pytest.raises(CheckFailedError) as error:
        harness.charm._generate_dex_auth_config()
    assert (
        error.value.msg
        == "Please add a connectors configuration to proceed without a static login."
    )
    assert error.value.status_type == BlockedStatus


@pytest.mark.parametrize(
    "dex_config",
    (
        {
            "enable-password-db": False,
            "port": 5555,
            "public-url": "dummy.url",
            "connectors": "test-connector",
        },
        {
            "enable-password-db": True,
            "port": 5555,
            "public-url": "dummy.url",
            "static-username": "new-user",
            "static-password": "new-pass",
        },
    ),
)
@patch("charm.Operator._update_layer")
@patch.object(Operator, "ensure_state", ensure_state)
@patch("charm.KubernetesServicePatch", lambda x, y: None)
def test_generate_dex_auth_config_returns(update_layer, dex_config, harness):
    """Check the method returns dex-auth configuration when different settings are provided."""
    harness.set_leader(True)
    harness.begin()
    harness.set_can_connect("dex", True)

    harness.update_config(dex_config)

    test_configuration = harness.charm._generate_dex_auth_config()
    assert test_configuration is not None


@patch("charm.KubernetesServicePatch", lambda x, y: None)
def test_disable_static_login_no_connector_blocked_status(harness):
    harness.set_leader(True)
    harness.begin()
    harness.set_can_connect("dex", True)

    config_updates = {
        "enable-password-db": False,
        "port": 5555,
        "public-url": "dummy.url",
    }

    harness.update_config(config_updates)
    assert isinstance(harness.charm.model.unit.status, BlockedStatus)


@patch("charm.KubernetesServicePatch", lambda x, y: None)
@patch("charm.Operator._update_layer")
def test_config_changed(update, harness):
    harness.set_leader(True)
    harness.begin()

    config_updates = {
        "enable-password-db": False,
        "port": 5555,
        "public-url": "dummy.url",
        "connectors": "connector01",
        "static-username": "new-user",
        "static-password": "new-pass",
    }

    harness.update_config(config_updates)

    update.assert_called()

    new_config = harness.model.config

    assert new_config == config_updates


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
