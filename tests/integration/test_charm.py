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
    dex_image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    await ops_test.model.deploy(
        my_charm, resources={"oci-image": dex_image_path}, trust=True, config=DEX_CONFIG
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=600
    )
    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"


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
                StatefulSet, APP_NAME, namespace=ops_test.model_name
            )

            expected_replicas = statefulset.spec.replicas
            ready_replicas = statefulset.status.readyReplicas

            assert expected_replicas == ready_replicas


@pytest.mark.abort_on_fail
async def test_relations(ops_test: OpsTest):
    oidc_gatekeeper = "oidc-gatekeeper"
    istio_pilot = "istio-pilot"
    istio_gateway = "istio-ingressgateway"

    await ops_test.model.deploy(
        entity_url=istio_pilot,
        channel="latest/edge",
        config={"default-gateway": "kubeflow-gateway"},
        trust=True,
    )

    await ops_test.model.deploy(
        entity_url="istio-gateway",
        application_name=istio_gateway,
        channel="latest/edge",
        config={"kind": "ingress"},
        trust=True,
    )
    await ops_test.model.add_relation(
        istio_pilot,
        istio_gateway,
    )

    await ops_test.model.wait_for_idle(
        [istio_pilot, istio_gateway],
        raise_on_blocked=False,
        status="active",
        timeout=90 * 10,
    )

    await ops_test.model.deploy(oidc_gatekeeper, channel="latest/edge", config=OIDC_CONFIG)
    await ops_test.model.add_relation(oidc_gatekeeper, APP_NAME)
    await ops_test.model.add_relation(f"{istio_pilot}:ingress", f"{APP_NAME}:ingress")
    await ops_test.model.add_relation(
        f"{istio_pilot}:ingress-auth",
        f"{oidc_gatekeeper}:ingress-auth",
    )

    await ops_test.model.deploy("kubeflow-profiles", channel="latest/edge", trust=True)
    await ops_test.model.deploy("kubeflow-dashboard", channel="latest/edge", trust=True)
    await ops_test.model.add_relation("kubeflow-profiles", "kubeflow-dashboard")
    await ops_test.model.add_relation(f"{istio_pilot}:ingress", "kubeflow-dashboard:ingress")

    # Set public-url for dex and oidc
    # Note: This could be affected by a race condition (if service has not received
    # an IP yet, this could fail) but probably this won't happen in practice
    # because that IP is delivered quickly and we wait_for_idle on the istio_gateway
    public_url = get_public_url(
        service_name="istio-ingressgateway-workload",
        namespace=ops_test.model_name,
    )
    log.info(f"got public_url of {public_url}")
    await ops_test.model.applications[APP_NAME].set_config({"public-url": public_url})
    await ops_test.model.applications["oidc-gatekeeper"].set_config({"public-url": public_url})

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
        [APP_NAME, "oidc-gatekeeper"],
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
    driver.find_element(By.ID, "login").send_keys(DEX_CONFIG["static-username"])
    driver.find_element(By.ID, "password").send_keys(DEX_CONFIG["static-password"])
    driver.find_element(By.ID, "submit-login").click()

    # Check if main page was loaded
    script = fix_queryselector(["main-page", "dashboard-view", "#Quick-Links"])
    wait.until(lambda x: x.execute_script(script))


async def test_prometheus_grafana_integration(ops_test: OpsTest):
    """Deploy prometheus, grafana and required relations, then test the metrics."""
    prometheus = "prometheus-k8s"
    grafana = "grafana-k8s"
    prometheus_scrape_charm = "prometheus-scrape-config-k8s"
    scrape_config = {"scrape_interval": "5s"}

    await ops_test.model.deploy(prometheus, channel="latest/beta", trust=True)
    await ops_test.model.deploy(grafana, channel="latest/beta", trust=True)
    await ops_test.model.add_relation(
        f"{prometheus}:grafana-dashboard", f"{grafana}:grafana-dashboard"
    )
    await ops_test.model.add_relation(
        f"{APP_NAME}:grafana-dashboard", f"{grafana}:grafana-dashboard"
    )
    await ops_test.model.deploy(
        prometheus_scrape_charm, channel="latest/beta", config=scrape_config
    )
    await ops_test.model.add_relation(APP_NAME, prometheus_scrape_charm)
    await ops_test.model.add_relation(
        f"{prometheus}:metrics-endpoint", f"{prometheus_scrape_charm}:metrics-endpoint"
    )

    await ops_test.model.wait_for_idle(status="active", timeout=60 * 10)

    status = await ops_test.model.get_status()
    prometheus_unit_ip = status["applications"][prometheus]["units"][f"{prometheus}/0"]["address"]
    log.info(f"Prometheus available at http://{prometheus_unit_ip}:9090")

    for attempt in retry_for_5_attempts:
        log.info(
            f"Testing prometheus deployment (attempt " f"{attempt.retry_state.attempt_number})"
        )
        with attempt:
            r = requests.get(
                f"http://{prometheus_unit_ip}:9090/api/v1/query?"
                f'query=up{{juju_application="{APP_NAME}"}}'
            )
            response = json.loads(r.content.decode("utf-8"))
            response_status = response["status"]
            log.info(f"Response status is {response_status}")
            assert response_status == "success"

            response_metric = response["data"]["result"][0]["metric"]
            assert response_metric["juju_application"] == APP_NAME
            assert response_metric["juju_model"] == ops_test.model_name


# Helper to retry calling a function over 30 seconds or 5 attempts
retry_for_5_attempts = Retrying(
    stop=(stop_after_attempt(5) | stop_after_delay(30)),
    wait=wait_exponential(multiplier=1, min=5, max=10),
    reraise=True,
)
