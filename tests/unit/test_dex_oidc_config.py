#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from charms.dex_auth.v0.dex_oidc_config import (
    DexOidcConfigObject,
    DexOidcConfigProvider,
    DexOidcConfigProviderWrapper,
    DexOidcConfigRelationDataMissingError,
    DexOidcConfigRelationMissingError,
    DexOidcConfigRequirer,
    DexOidcConfigRequirerWrapper,
    DexOidcConfigUpdatedEvent,
)
from charms.harness_extensions.v0.capture_events import capture
from ops.charm import CharmBase
from ops.model import TooManyRelatedAppsError
from ops.testing import Harness

TEST_RELATION_NAME = "test-relation"
REQUIRER_CHARM_META = f"""
name: requirer-test-charm
requires:
  {TEST_RELATION_NAME}:
    interface: test-interface
"""
PROVIDER_CHARM_META = f"""
name: provider-test-charm
provides:
  {TEST_RELATION_NAME}:
    interface: test-interface
"""


class GenericCharm(CharmBase):
    pass


@pytest.fixture()
def requirer_charm_harness():
    return Harness(GenericCharm, meta=REQUIRER_CHARM_META)


@pytest.fixture()
def provider_charm_harness():
    return Harness(GenericCharm, meta=PROVIDER_CHARM_META)


def test_requirer_get_data_from_requirer(requirer_charm_harness):
    """Assert the relation data is as expected."""
    # Initial configuration
    requirer_charm_harness.set_model_name("test-model")
    requirer_charm_harness.set_leader(True)
    requirer_charm_harness.begin()

    # Instantiate DexOidcConfigRequirerWrapper class
    requirer_charm_harness.charm._k8s_svc_info_requirer = DexOidcConfigRequirer(
        requirer_charm_harness.charm, relation_name=TEST_RELATION_NAME
    )

    # Add and update relation
    expected_data = DexOidcConfigObject(issuer_url="http://my-dex.io/dex")
    data_dict = {"issuer-url": expected_data.issuer_url}
    requirer_charm_harness.add_relation(TEST_RELATION_NAME, "app", app_data=data_dict)

    # Get the relation data
    actual_relation_data = requirer_charm_harness.charm._k8s_svc_info_requirer.get_data()

    # Assert returns dictionary with expected values
    assert actual_relation_data == expected_data


def test_get_dex_oidc_config_on_refresh_event(requirer_charm_harness):
    """Test the Provider correctly handles the event set in refresh_event."""
    # Initial configuration
    requirer_charm_harness.set_model_name("test-model")
    requirer_charm_harness.set_leader(True)
    requirer_charm_harness.begin()

    # Instantiate DexOidcConfigRequirerWrapper class
    requirer_charm_harness.charm._k8s_svc_info_requirer = DexOidcConfigRequirer(
        requirer_charm_harness.charm,
        relation_name=TEST_RELATION_NAME,
        refresh_event=requirer_charm_harness.charm.on[TEST_RELATION_NAME].relation_joined,
    )

    # Add and update relation
    expected_data = DexOidcConfigObject(issuer_url="http://my-dex.io/dex")
    data_dict = {"issuer_url": expected_data.issuer_url}
    rel_id = requirer_charm_harness.add_relation(TEST_RELATION_NAME, "app", app_data=data_dict)
    relation = requirer_charm_harness.charm.framework.model.get_relation(
        TEST_RELATION_NAME, rel_id
    )

    # Assert that we emit an event for data being updated
    with capture(requirer_charm_harness, DexOidcConfigUpdatedEvent):
        requirer_charm_harness.charm.on[TEST_RELATION_NAME].relation_joined.emit(relation)


def test_check_raise_too_many_relations(requirer_charm_harness):
    """Assert that TooManyRelatedAppsError is raised if more than one application is related."""
    requirer_charm_harness.set_model_name("test-model")
    requirer_charm_harness.begin()
    requirer_charm_harness.set_leader(True)

    # Instantiate DexOidcConfigRequirerWrapper class
    requirer_charm_harness.charm._k8s_svc_info_requirer = DexOidcConfigRequirerWrapper(
        requirer_charm_harness.charm, relation_name=TEST_RELATION_NAME
    )

    requirer_charm_harness.add_relation(TEST_RELATION_NAME, "app")
    requirer_charm_harness.add_relation(TEST_RELATION_NAME, "app2")

    with pytest.raises(TooManyRelatedAppsError):
        requirer_charm_harness.charm._k8s_svc_info_requirer.get_data()


def test_validate_relation_raise_no_relation(requirer_charm_harness):
    """Assert that DexOidcConfigRelationMissingError is raised in the absence of the relation."""
    requirer_charm_harness.set_model_name("test-model")
    requirer_charm_harness.begin()
    requirer_charm_harness.set_leader(True)

    # Instantiate DexOidcConfigRequirerWrapper class
    requirer_charm_harness.charm._k8s_svc_info_requirer = DexOidcConfigRequirerWrapper(
        requirer_charm_harness.charm, relation_name=TEST_RELATION_NAME
    )

    with pytest.raises(DexOidcConfigRelationMissingError):
        requirer_charm_harness.charm._k8s_svc_info_requirer.get_data()


def test_validate_relation_raise_no_relation_data(requirer_charm_harness):
    """Assert that DexOidcConfigRelationDataMissingError is raised in the absence of relation data."""  # noqa
    requirer_charm_harness.set_model_name("test-model")
    requirer_charm_harness.begin()
    requirer_charm_harness.set_leader(True)

    # Instantiate DexOidcConfigRequirerWrapper class
    requirer_charm_harness.charm._k8s_svc_info_requirer = DexOidcConfigRequirerWrapper(
        requirer_charm_harness.charm, relation_name=TEST_RELATION_NAME
    )

    requirer_charm_harness.add_relation(TEST_RELATION_NAME, "app")

    with pytest.raises(DexOidcConfigRelationDataMissingError) as error:
        requirer_charm_harness.charm._k8s_svc_info_requirer.get_data()
    assert str(error.value) == f"No data found in relation {TEST_RELATION_NAME} data bag."


def test_provider_sends_data_automatically_passes(provider_charm_harness):
    """Assert the relation data is passed automatically by the provider."""
    provider_charm_harness.set_model_name("test-model")
    provider_charm_harness.set_leader(True)
    provider_charm_harness.begin()

    # Instantiate the DexOidcConfigProvider
    issuer_url = "http://my-dex.io/dex"
    provider_charm_harness.charm._k8s_svc_info_provider = DexOidcConfigProvider(
        charm=provider_charm_harness.charm,
        issuer_url=issuer_url,
        relation_name=TEST_RELATION_NAME,
    )

    # Add corresponding relation
    provider_charm_harness.add_relation(TEST_RELATION_NAME, "app")

    # Check the relation data
    relations = provider_charm_harness.model.relations[TEST_RELATION_NAME]
    for relation in relations:
        actual_relation_data = relation.data[provider_charm_harness.charm.app]
        # Assert returns dictionary with expected values
        assert actual_relation_data.get("issuer-url") == issuer_url


def test_requirer_wrapper_get_data_passes(requirer_charm_harness):
    """Assert the relation data is as expected."""
    # Initial configuration
    requirer_charm_harness.set_model_name("test-model")
    requirer_charm_harness.set_leader(True)
    requirer_charm_harness.begin()

    # Add and update relation
    data_dict = {"issuer-url": "http://my-dex.io/dex"}
    requirer_charm_harness.add_relation(TEST_RELATION_NAME, "app", app_data=data_dict)

    # Instantiate DexOidcConfigRequirerWrapper class
    requirer_charm_harness.charm._k8s_svc_info_requirer = DexOidcConfigRequirerWrapper(
        requirer_charm_harness.charm, relation_name=TEST_RELATION_NAME
    )

    # Get the relation data
    expected_data = DexOidcConfigObject(issuer_url=data_dict["issuer-url"])
    actual_relation_data = requirer_charm_harness.charm._k8s_svc_info_requirer.get_data()

    # Assert returns dictionary with expected values
    assert actual_relation_data == expected_data


def test_provider_wrapper_send_data_passes(provider_charm_harness):
    """Assert the relation data is as expected by the provider wrapper."""
    # Initial configuration
    provider_charm_harness.set_model_name("test-model")
    provider_charm_harness.begin()
    provider_charm_harness.set_leader(True)
    provider_charm_harness.add_relation(TEST_RELATION_NAME, "app")

    # Instantiate DexOidcConfigProviderWrapper class
    provider_charm_harness.charm._k8s_svc_info_provider = DexOidcConfigProviderWrapper(
        provider_charm_harness.charm,
        relation_name=TEST_RELATION_NAME,
    )

    # Send relation data
    relation_data = DexOidcConfigObject(issuer_url="http://my-dex.io/dex")
    provider_charm_harness.charm._k8s_svc_info_provider.send_data(
        issuer_url=relation_data.issuer_url
    )
    relations = provider_charm_harness.model.relations[TEST_RELATION_NAME]
    for relation in relations:
        actual_relation_data = relation.data[provider_charm_harness.charm.app]
        # Assert returns dictionary with expected values
        assert actual_relation_data.get("issuer-url") == relation_data.issuer_url
