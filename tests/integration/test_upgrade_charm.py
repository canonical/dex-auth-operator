import logging
from pathlib import Path

import yaml

import pytest
import serialized_data_interface.local_sdi as local_sdi

log = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    local_charm = await ops_test.build_charm(".")
    local_sdi.main()
    await ops_test.model.deploy("cs:dex-auth")
    await ops_test.model.wait_for_idle()

    charm_name = METADATA["name"]
    image_path = METADATA["resources"]["oci-image"]["upstream-source"]

    app = ops_test.model.applications[charm_name]
    resources = {"oci-image": image_path}
    await app.refresh(path=local_charm, resources=resources)
    await ops_test.model.wait_for_idle()


async def test_status(ops_test):
    charm_name = METADATA["name"]
    assert ops_test.model.applications[charm_name].units[0].workload_status == "active"
