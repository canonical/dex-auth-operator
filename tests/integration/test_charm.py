# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path
from time import sleep

import lightkube
import pytest
import requests
import yaml
from lightkube.resources.apps_v1 import StatefulSet
from lightkube.resources.core_v1 import Service
from pytest_operator.plugin import OpsTest
from selenium import webdriver
from selenium.common.exceptions import JavascriptException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from tenacity import Retrying, stop_after_attempt, stop_after_delay, wait_exponential

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_ROOT = "."
DEX_AUTH = "dex-auth"
DEX_AUTH_APP_NAME = METADATA["name"]
DEX_AUTH_TRUST = True
DEX_AUTH_CONFIG = {
    "static-username": "admin",
    "static-password": "foobar",
}

OIDC_GATEKEEPER = "oidc-gatekeeper"
OIDC_GATEKEEPER_CHANNEL = "ckf-1.8/stable"
OIDC_GATEKEEPER_CONFIG = {
    "client-name": "Ambassador Auth OIDC",
    "client-secret": "oidc-client-secret",
}

ISTIO_OPERATORS_CHANNEL = "1.17/stable"
ISTIO_PILOT = "istio-pilot"
ISTIO_PILOT_TRUST = True
ISTIO_PILOT_CONFIG = {"default-gateway": "kubeflow-gateway"}
ISTIO_GATEWAY = "istio-gateway"
ISTIO_GATEWAY_APP_NAME = "istio-ingressgateway"
ISTIO_GATEWAY_TRUST = True
ISTIO_GATEWAY_CONFIG = {"kind": "ingress"}

KUBEFLOW_PROFILES = "kubeflow-profiles"
KUBEFLOW_PROFILES_CHANNEL = "1.8/stable"
KUBEFLOW_PROFILES_TRUST = True

KUBEFLOW_DASHBOARD = "kubeflow-dashboard"
KUBEFLOW_DASHBOARD_CHANNEL = "1.8/stable"
KUBEFLOW_DASHBOARD_TRUST = True

PROMETHEUS_K8S = "prometheus-k8s"
PROMETHEUS_K8S_CHANNEL = "1.0/stable"
PROMETHEUS_K8S_TRUST = True
GRAFANA_K8S = "grafana-k8s"
GRAFANA_K8S_CHANNEL = "1.0/stable"
GRAFANA_K8S_TRUST = True
PROMETHEUS_SCRAPE_K8S = "prometheus-scrape-config-k8s"
PROMETHEUS_SCRAPE_K8S_CHANNEL = "1.0/stable"
PROMETHEUS_SCRAPE_CONFIG = {"scrape_interval": "30s"}
log = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    my_charm = await ops_test.build_charm(".")
    dex_image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    await ops_test.model.deploy(
        my_charm,
        resources={"oci-image": dex_image_path},
        trust=DEX_AUTH_TRUST,
        config=DEX_AUTH_CONFIG,
    )
    await ops_test.model.wait_for_idle(
        apps=[DEX_AUTH_APP_NAME], status="active", raise_on_blocked=True, timeout=600
    )
    assert ops_test.model.applications[DEX_AUTH_APP_NAME].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
def test_statefulset_readiness(ops_test: OpsTest):
    lightkube_client = lightkube.Client()
    for attempt in retry_for_5_attempts:
        log.info(
            f"Waiting for StatefulSet replica(s) to be ready"
            f"(attempt {attempt.retry_state.attempt_number})"
        )
        with attempt:
            statefulset = lightkube_client.get(
                StatefulSet, DEX_AUTH_APP_NAME, namespace=ops_test.model_name
            )

            expected_replicas = statefulset.spec.replicas
            ready_replicas = statefulset.status.readyReplicas

            assert expected_replicas == ready_replicas


@pytest.mark.abort_on_fail
async def test_relations(ops_test: OpsTest):
    await ops_test.model.deploy(
        entity_url=ISTIO_PILOT,
        channel=ISTIO_OPERATORS_CHANNEL,
        config=ISTIO_PILOT_CONFIG,
        trust=ISTIO_PILOT_TRUST,
    )

    await ops_test.model.deploy(
        entity_url=ISTIO_GATEWAY,
        application_name=ISTIO_GATEWAY_APP_NAME,
        channel=ISTIO_OPERATORS_CHANNEL,
        config=ISTIO_GATEWAY_CONFIG,
        trust=ISTIO_GATEWAY_TRUST,
    )
    await ops_test.model.add_relation(
        ISTIO_PILOT,
        ISTIO_GATEWAY_APP_NAME,
    )

    await ops_test.model.wait_for_idle(
        [ISTIO_PILOT, ISTIO_GATEWAY_APP_NAME],
        raise_on_blocked=False,
        status="active",
        timeout=90 * 10,
    )

    await ops_test.model.deploy(
        OIDC_GATEKEEPER,
        channel=OIDC_GATEKEEPER_CHANNEL,
        config=OIDC_GATEKEEPER_CONFIG,
    )
    await ops_test.model.add_relation(OIDC_GATEKEEPER, DEX_AUTH_APP_NAME)
    await ops_test.model.add_relation(f"{ISTIO_PILOT}:ingress", f"{DEX_AUTH_APP_NAME}:ingress")
    await ops_test.model.add_relation(
        f"{ISTIO_PILOT}:ingress-auth",
        f"{OIDC_GATEKEEPER}:ingress-auth",
    )

    await ops_test.model.deploy(
        KUBEFLOW_PROFILES,
        channel=KUBEFLOW_PROFILES_CHANNEL,
        trust=KUBEFLOW_PROFILES_TRUST,
    )
    await ops_test.model.deploy(
        KUBEFLOW_DASHBOARD,
        channel=KUBEFLOW_DASHBOARD_CHANNEL,
        trust=KUBEFLOW_DASHBOARD_TRUST,
    )
    await ops_test.model.add_relation(KUBEFLOW_PROFILES, KUBEFLOW_DASHBOARD)
    await ops_test.model.add_relation(f"{ISTIO_PILOT}:ingress", f"{KUBEFLOW_DASHBOARD,}:ingress")

    # Set public-url for dex and oidc
    # Note: This could be affected by a race condition (if service has not received
    # an IP yet, this could fail) but probably this won't happen in practice
    # because that IP is delivered quickly and we wait_for_idle on the istio_gateway
    public_url = get_public_url(
        service_name="istio-ingressgateway-workload",
        namespace=ops_test.model_name,
    )
    log.info(f"got public_url of {public_url}")
    await ops_test.model.applications[DEX_AUTH_APP_NAME].set_config({"public-url": public_url})
    await ops_test.model.applications[OIDC_GATEKEEPER].set_config({"public-url": public_url})

    await ops_test.model.wait_for_idle(
        status="active",
        raise_on_blocked=False,
        raise_on_error=True,
        timeout=600,
    )


def get_public_url(service_name: str, namespace: str):
    lightkube_client = lightkube.Client()
    gateway_svc = lightkube_client.get(Service, service_name, namespace=namespace)

    endpoint = gateway_svc.status.loadBalancer.ingress[0].ip
    url = f"http://{endpoint}.nip.io"
    return url


@pytest.fixture()
async def driver(ops_test: OpsTest):
    public_url = get_public_url(
        service_name="istio-ingressgateway-workload",
        namespace=ops_test.model_name,
    )

    # Oidc may get blocked and recreate the unit
    await ops_test.model.wait_for_idle(
        [DEX_AUTH_APP_NAME, OIDC_GATEKEEPER],
        status="active",
        raise_on_blocked=False,
        raise_on_error=False,
        timeout=600,
    )

    options = Options()
    options.headless = True

    with webdriver.Chrome(options=options) as driver:
        driver.delete_all_cookies()
        wait = WebDriverWait(driver, 180, 1, (JavascriptException, StopIteration))
        for _ in range(60):
            try:
                driver.get(public_url)
                break
            except WebDriverException:
                sleep(5)
        else:
            driver.get(public_url)

        yield driver, wait, public_url

        driver.get_screenshot_as_file("/tmp/selenium-dashboard.png")


def fix_queryselector(elems):
    selectors = '").shadowRoot.querySelector("'.join(elems)
    return 'return document.querySelector("' + selectors + '")'


def test_login(driver):
    driver, wait, url = driver

    driver.get_screenshot_as_file("/tmp/selenium-logon.png")
    # Log in using dex credentials
    driver.find_element(By.ID, "login").send_keys(DEX_AUTH_CONFIG["static-username"])
    driver.find_element(By.ID, "password").send_keys(DEX_AUTH_CONFIG["static-password"])
    driver.find_element(By.ID, "submit-login").click()

    # Check if main page was loaded
    script = fix_queryselector(["main-page", "dashboard-view", "#Quick-Links"])
    wait.until(lambda x: x.execute_script(script))


async def test_prometheus_grafana_integration(ops_test: OpsTest):
    """Deploy prometheus, grafana and required relations, then test the metrics."""
    # Deploy and relate prometheus
    await ops_test.model.deploy(
        PROMETHEUS_K8S,
        channel=PROMETHEUS_K8S_CHANNEL,
        trust=PROMETHEUS_K8S_TRUST,
    )
    await ops_test.model.deploy(
        GRAFANA_K8S,
        channel=GRAFANA_K8S_CHANNEL,
        trust=GRAFANA_K8S_TRUST,
    )
    await ops_test.model.deploy(
        PROMETHEUS_SCRAPE_K8S,
        channel=PROMETHEUS_SCRAPE_K8S_CHANNEL,
        config=PROMETHEUS_SCRAPE_CONFIG,
    )

    await ops_test.model.add_relation(DEX_AUTH_APP_NAME, PROMETHEUS_SCRAPE_K8S)
    await ops_test.model.add_relation(
        f"{PROMETHEUS_K8S}:grafana-dashboard",
        f"{GRAFANA_K8S}:grafana-dashboard",
    )
    await ops_test.model.add_relation(
        f"{DEX_AUTH_APP_NAME}:grafana-dashboard",
        f"{GRAFANA_K8S}:grafana-dashboard",
    )
    await ops_test.model.add_relation(
        f"{PROMETHEUS_K8S}:metrics-endpoint",
        f"{PROMETHEUS_SCRAPE_K8S}:metrics-endpoint",
    )

    await ops_test.model.wait_for_idle(status="active", timeout=60 * 20)

    status = await ops_test.model.get_status()
    prometheus_unit_ip = status["applications"][PROMETHEUS_K8S]["units"][f"{PROMETHEUS_K8S}/0"][
        "address"
    ]
    log.info(f"Prometheus available at http://{prometheus_unit_ip}:9090")

    for attempt in retry_for_5_attempts:
        log.info(
            f"Testing prometheus deployment (attempt " f"{attempt.retry_state.attempt_number})"
        )
        with attempt:
            r = requests.get(
                f"http://{prometheus_unit_ip}:9090/api/v1/query?"
                f'query=up{{juju_application="{DEX_AUTH_APP_NAME}"}}'
            )
            response = json.loads(r.content.decode("utf-8"))
            response_status = response["status"]
            log.info(f"Response status is {response_status}")
            assert response_status == "success"

            response_metric = response["data"]["result"][0]["metric"]
            assert response_metric["juju_application"] == DEX_AUTH_APP_NAME
            assert response_metric["juju_model"] == ops_test.model_name

            # Assert the unit is available by checking the query result
            # The data is presented as a list [1707357912.349, '1'], where the
            # first value is a timestamp and the second value is the state of the unit
            # 1 means available, 0 means unavailable
            assert response["data"]["result"][0]["value"][1] == "1"


# Helper to retry calling a function over 30 seconds or 5 attempts
retry_for_5_attempts = Retrying(
    stop=(stop_after_attempt(5) | stop_after_delay(30)),
    wait=wait_exponential(multiplier=1, min=5, max=10),
    reraise=True,
)
