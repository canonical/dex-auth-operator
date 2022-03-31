import logging
from pathlib import Path

import yaml

import pytest
import requests
from lightkube.core.client import Client
from lightkube.resources.rbac_authorization_v1 import Role
from lightkube.models.rbac_v1 import PolicyRule
import time
from pytest_operator.plugin import OpsTest

log = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
DEX_CONFIG = {
    "static-username": "admin",
    "static-password": "foobar",
}


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    my_charm = await ops_test.build_charm(".")
    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}
    await ops_test.model.deploy(my_charm, resources=resources, config=DEX_CONFIG)
    await ops_test.model.wait_for_idle(status="active")


async def test_status(ops_test):
    charm_name = METADATA["name"]
    assert ops_test.model.applications[charm_name].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
async def test_access_login_page(ops_test: OpsTest):
    oidc = "oidc-gatekeeper"
    istio = "istio-pilot"
    istio_gateway = "istio-gateway"
    dex = METADATA["name"]

    oidc_config = {
        "client-id": "authservice-oidc",
        "client-name": "Ambassador Auth OIDC",
        "client-secret": "oidc-client-secret",
        "oidc-scopes": "openid profile email groups",
    }
    # await ops_test.model.deploy(oidc, config=oidc_config)
    await ops_test.model.deploy(
        oidc,
        channel="latest/edge",
        config=oidc_config,
    )
    await ops_test.model.deploy(istio, channel="1.5/stable")
    await ops_test.model.deploy(istio_gateway, channel="1.5/stable", trust=True)
    await ops_test.model.add_relation(oidc, dex)
    await ops_test.model.add_relation(istio, istio_gateway)
    await ops_test.model.add_relation(f"{istio}:ingress", f"{dex}:ingress")
    await ops_test.model.add_relation(f"{istio}:ingress", f"{oidc}:ingress")
    await ops_test.model.add_relation(f"{istio}:ingress-auth", f"{oidc}:ingress-auth")

    await ops_test.model.wait_for_idle(
        [istio_gateway],
        status="waiting",
        timeout=600,
    )

    lightkube_client = Client(
        namespace=ops_test.model_name,
    )

    await ops_test.model.set_config({"update-status-hook-interval": "15s"})
    istio_gateway_role_name = "istio-gateway-operator"

    new_policy_rule = PolicyRule(verbs=["*"], apiGroups=["*"], resources=["*"])
    this_role = lightkube_client.get(Role, istio_gateway_role_name)
    this_role.rules.append(new_policy_rule)
    lightkube_client.patch(Role, istio_gateway_role_name, this_role)

    time.sleep(50)
    await ops_test.model.set_config({"update-status-hook-interval": "5m"})

    await ops_test.model.wait_for_idle(
        [dex, oidc, istio, istio_gateway],
        status="active",
        timeout=3500,
    )

    status = await ops_test.model.get_status()
    public_url = (
        f"http://{status['applications'][istio_gateway]['public-address']}.nip.io"
    )

    await ops_test.model.applications[dex].set_config({"public-url": public_url})
    await ops_test.model.applications[oidc].set_config({"public-url": public_url})

    await ops_test.model.wait_for_idle(
        [dex, oidc, istio, istio_gateway],
        status="active",
        raise_on_blocked=True,
        raise_on_error=True,
        timeout=600,
    )

    url = f"{public_url}/dex"
    for _ in range(60):
        try:
            requests.get(url, timeout=60)
            break
        except requests.ConnectionError:
            time.sleep(5)
    r = requests.get(url)
    assert r.status_code == 200
