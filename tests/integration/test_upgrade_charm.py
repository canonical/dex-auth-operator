# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest

log = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    local_charm = await ops_test.build_charm(".")
    await ops_test.model.deploy("ch:dex-auth", "dex-auth-operator", trust=True)
    await ops_test.model.wait_for_idle(status="active")

    await ops_test.juju("upgrade-charm", "dex-auth-operator", "--path", local_charm)
    await ops_test.model.wait_for_idle(status="active")


async def test_status(ops_test):
    for app in ops_test.model.applications.values():
        for unit in app.units:
            assert unit.workload_status == "active"
