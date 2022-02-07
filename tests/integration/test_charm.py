# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

log = logging.getLogger(__name__)
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
DEX_CONFIG = {
    "static-username": "admin",
    "static-password": "foobar",
}
OIDC_CONFIG = {
    "client-name": "Ambassador Auth OIDC",
    "client-secret": "oidc-client-secret",
}


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    my_charm = await ops_test.build_charm(".")
    await ops_test.model.deploy(my_charm, trust=True, config=DEX_CONFIG)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=600
    )
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
async def test_relations(ops_test: OpsTest):
    oidc_gatekeeper = "oidc-gatekeeper"
    istio_pilot = "istio-pilot"
    await ops_test.model.deploy(oidc_gatekeeper, config=OIDC_CONFIG)
    await ops_test.model.deploy(istio_pilot, channel="1.5/stable")
    await ops_test.model.add_relation(oidc_gatekeeper, APP_NAME)
    await ops_test.model.add_relation(f"{istio_pilot}:ingress", f"{APP_NAME}:ingress")

    await ops_test.model.wait_for_idle(
        [APP_NAME, oidc_gatekeeper, istio_pilot],
        status="active",
        # TODO: https://github.com/canonical/dex-auth-operator/issues/37
        # raise_on_blocked=True,
        raise_on_error=True,
        timeout=600,
    )
