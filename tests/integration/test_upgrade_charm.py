import logging
from pathlib import Path

import pytest
import yaml

log = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    local_charm = await ops_test.build_charm(".")
    await ops_test.model.deploy("cs:dex-auth")
    await ops_test.model.wait_for_idle()

    image_path = METADATA["resources"]["oci-image"]["upstream-source"]

    app = ops_test.model.applications[APP_NAME]
    resources = {"oci-image": image_path}
    await app.refresh(path=local_charm, resources=resources)
    await ops_test.model.wait_for_idle()


async def test_status(ops_test):
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"
