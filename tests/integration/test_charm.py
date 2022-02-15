import logging
from pathlib import Path

import yaml

import pytest
import requests

log = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    my_charm = await ops_test.build_charm(".")
    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}
    await ops_test.model.deploy(my_charm, resources=resources)
    await ops_test.model.wait_for_idle()


async def test_status(ops_test):
    charm_name = METADATA["name"]
    assert ops_test.model.applications[charm_name].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
async def test_access_login_page(ops_test):
    oidc = "oidc-gatekeeper"
    istio = "istio-pilot"
    dex = METADATA["name"]
    status = await ops_test.model.get_status()
    dex_ip = status["applications"][dex]["public-address"]
    oidc_config = {
        "client-id": "authservice-oidc",
        "client-name": "Ambassador Auth OIDC",
        "client-secret": "oidc-client-secret",
        "oidc-scopes": "openid profile email groups",
        "public-url": dex_ip,
    }
    await ops_test.model.deploy(oidc, config=oidc_config)
    await ops_test.model.deploy(istio, channel="1.5/stable")
    await ops_test.model.add_relation(oidc, dex)
    await ops_test.model.add_relation(f"{istio}:ingress", f"{dex}:ingress")
    await ops_test.model.add_relation(f"{istio}:ingress", f"{oidc}:ingress")
    await ops_test.model.add_relation(f"{istio}:ingress-auth", f"{oidc}:ingress-auth")

    await ops_test.model.wait_for_idle(
        [dex, oidc, istio],
        status="active",
        raise_on_blocked=False,
        # oidc transient errors when update public url
        raise_on_error=False,
        timeout=600,
    )

    url = f"http://{dex_ip}:5556/dex/auth?client_id={oidc_config['client-id']}&redirect_uri=%2Fauthservice%2Foidc%2Fcallback&response_type=code&scope={oidc_config['oidc-scopes'].replace(' ', '+')}&state="
    r = requests.get(url)
    assert r.status_code == 200
