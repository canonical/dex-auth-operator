# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import pytest
import yaml
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

from charm import Operator

DEX_OIDC_CONFIG_RELATION = "dex-oidc-config"


@pytest.fixture
def harness():
    return Harness(Operator)


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
def test_log_forwarding(harness):
    with patch("charm.LogForwarder") as mock_logging:
        harness.begin()
        mock_logging.assert_called_once_with(charm=harness.charm)


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
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


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
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


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
def test_generate_dex_auth_config_raises(harness):
    """Check the method raises when static login is disabled and no connectors are provided."""
    harness.begin()
    config_updates = {
        "enable-password-db": False,
        "port": 5555,
    }

    harness.update_config(config_updates)

    with pytest.raises(ErrorWithStatus) as error:
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
            "connectors": "test-connector",
        },
        {
            "enable-password-db": True,
            "port": 5555,
            "static-username": "new-user",
            "static-password": "new-pass",
        },
    ),
)
@patch("charm.Operator._update_layer")
@patch.object(Operator, "ensure_state", ensure_state)
@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
def test_generate_dex_auth_config_returns(update_layer, dex_config, harness):
    """Check the method returns dex-auth configuration when different settings are provided."""
    harness.set_leader(True)
    harness.begin()
    harness.set_can_connect("dex", True)

    harness.update_config(dex_config)

    test_configuration = harness.charm._generate_dex_auth_config()
    assert test_configuration is not None

    test_configuration_dict = yaml.safe_load(test_configuration)
    assert (
        yaml.safe_load(harness.model.config["connectors"]) == test_configuration_dict["connectors"]
    )
    assert (
        harness.model.config["enable-password-db"] == test_configuration_dict["enablePasswordDB"]
    )

    static_passwords = test_configuration_dict.get("staticPasswords")
    assert isinstance(static_passwords, list)
    if not harness.model.config["static-username"]:
        assert len(static_passwords) == 0
    else:
        assert len(static_passwords) == 1
        assert harness.model.config["static-username"] == static_passwords[0].get("username")


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
def test_disable_static_login_no_connector_blocked_status(harness):
    harness.set_leader(True)
    harness.begin()
    harness.set_can_connect("dex", True)

    config_updates = {
        "enable-password-db": False,
        "port": 5555,
    }

    harness.update_config(config_updates)
    assert isinstance(harness.charm.model.unit.status, BlockedStatus)


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
@patch("charm.Operator._update_layer")
def test_config_changed(update, harness):
    harness.set_leader(True)
    harness.begin()

    config_updates = {
        "enable-password-db": False,
        "issuer-url": "http://my-dex.io/dex",
        "port": 5555,
        "connectors": "connector01",
        "static-username": "new-user",
        "static-password": "new-pass",
        "public-url": "",
    }

    harness.update_config(config_updates)

    update.assert_called()

    new_config = harness.model.config

    assert new_config == config_updates


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
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


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
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


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
def test_dex_oidc_config_with_data(harness):
    """Test the relation data has values by default as the charm is broadcasting them."""
    harness.set_leader(True)
    harness.begin()

    rel_id = harness.add_relation(DEX_OIDC_CONFIG_RELATION, "app")
    rel_data = harness.model.get_relation(DEX_OIDC_CONFIG_RELATION, rel_id).data[harness.model.app]

    # Default values are expected
    expected_url = f"http://{harness.model.app.name}.{harness.model.name}.svc:5556/dex"
    assert rel_data["issuer-url"] == expected_url


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
def test_grpc_relation_with_data_when_data_changes(
    harness,
):
    """Test the relation data on config changed events."""
    harness.set_leader(True)
    # Change the configuration option before starting harness so
    # the correct values are passed to the DexOidcConfig lib
    # FIXME: the correct behaviour should be to change the config
    # at any point in time to trigger config events and check the
    # value gets passed correctly when it changes.
    harness.update_config({"issuer-url": "http://my-dex.io/dex"})
    harness.begin()

    # Initialise a dex-oidc-config requirer charm
    #    harness.charm.leadership_gate.get_status = MagicMock(return_value=ActiveStatus())
    #    harness.charm.kubernetes_resources.get_status = MagicMock(return_value=ActiveStatus())

    # Add relation between the requirer charm and this charm (dex-auth)
    provider_rel_id = harness.add_relation(
        relation_name=DEX_OIDC_CONFIG_RELATION, remote_app="other-app"
    )
    provider_rel_data = harness.get_relation_data(
        relation_id=provider_rel_id, app_or_unit=harness.charm.app.name
    )

    # Change the port of the service and check the value changes
    assert provider_rel_data["issuer-url"] == harness.model.config["issuer-url"]


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
@pytest.mark.parametrize(
    "issuer_url_config, default_value, expected_result",
    (
        (None, True, None),
        ("http://my-dex.io:5557/dex", False, "http://my-dex.io:5557/dex"),
    ),
)
def test_issuer_url_property_with_issuer_url_config(
    issuer_url_config, default_value, expected_result, harness
):
    """Test the property returns as expected.


    The first case assumes the issuer-url config option is not set, thus the default-value
    of "http://dex-auth.<namespace>.svc:5556/dex" should be returned; the second case should return
    the value set in the config option.
    """
    harness.set_leader(True)
    harness.update_config({"issuer-url": issuer_url_config})
    harness.begin()

    if default_value:
        expected_result = f"http://{harness.model.app.name}.{harness.model.name}.svc:5556/dex"
    assert harness.charm._issuer_url == expected_result


@patch("charm.KubernetesServicePatch", lambda *_, **__: None)
@pytest.mark.parametrize(
    "public_url_config, expected_result",
    (
        ("http://my-dex.io:5557", "http://my-dex.io:5557/dex"),
        ("my-dex.io:5557", "http://my-dex.io:5557/dex"),
    ),
)
def test_issuer_url_property_with_public_url_config(public_url_config, expected_result, harness):
    """Test the property returns as expected."""
    harness.set_leader(True)
    harness.update_config({"public-url": public_url_config})
    harness.begin()
    assert harness.charm._issuer_url == expected_result
