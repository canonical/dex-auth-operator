# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path
from time import sleep

import lightkube
import pytest
import yaml
from charmed_kubeflow_chisme.testing import (
    GRAFANA_AGENT_APP,
    assert_alert_rules,
    assert_logging,
    assert_metrics_endpoint,
    deploy_and_assert_grafana_agent,
    deploy_and_integrate_service_mesh_charms,
    generate_container_securitycontext_map,
    get_alert_rules,
    integrate_with_service_mesh,
)
from charmed_kubeflow_chisme.testing.ambient_integration import get_ingress_external_ip
from charms_dependencies import KUBEFLOW_DASHBOARD, KUBEFLOW_PROFILES, OIDC_GATEKEEPER
from lightkube.resources.apps_v1 import StatefulSet
from pytest_operator.plugin import OpsTest
from selenium import webdriver
from selenium.common.exceptions import JavascriptException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from tenacity import Retrying, stop_after_attempt, stop_after_delay, wait_exponential

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CONTAINERS_SECURITY_CONTEXT_MAP = generate_container_securitycontext_map(METADATA)
CHARM_ROOT = "."
DEX_AUTH_APP_NAME = METADATA["name"]
DEX_AUTH_TRUST = True
DEX_AUTH_CONFIG = {
    "static-username": "admin",
    "static-password": "foobar",
}
ISTIO_K8S = "istio-k8s"
ISTIO_INGRESS_K8S = "istio-ingress-k8s"

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

    # Deploying grafana-agent-k8s and add all relations
    await deploy_and_assert_grafana_agent(
        ops_test.model, DEX_AUTH_APP_NAME, metrics=True, dashboard=True, logging=True
    )


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
    await deploy_and_integrate_service_mesh_charms(
        DEX_AUTH_APP_NAME,
        ops_test.model,
        model_on_mesh=False,
        relate_to_beacon=True,
        relate_to_ingress_route_endpoint=False,
    )

    # relate istio-ingress-k8s and istio-k8s, for handling the forward-auth relation
    await ops_test.model.integrate(
        f"{ISTIO_K8S}",
        f"{ISTIO_INGRESS_K8S}",
    )

    # manually relate to the unauthenticated endpoint
    await ops_test.model.integrate(
        f"{DEX_AUTH_APP_NAME}:istio-ingress-route-unauthenticated",
        f"{ISTIO_INGRESS_K8S}:istio-ingress-route-unauthenticated",
    )

    # oidc-gatekeeper
    await ops_test.model.deploy(
        OIDC_GATEKEEPER.charm,
        channel=OIDC_GATEKEEPER.channel,
        config=OIDC_GATEKEEPER.config,
        trust=True,
    )

    await integrate_with_service_mesh(
        OIDC_GATEKEEPER.charm, ops_test.model, relate_to_ingress_route_endpoint=False
    )

    await ops_test.model.add_relation(
        f"{OIDC_GATEKEEPER.charm}:dex-oidc-config",
        f"{DEX_AUTH_APP_NAME}:dex-oidc-config",
    )
    await ops_test.model.add_relation(
        f"{OIDC_GATEKEEPER.charm}:oidc-client", f"{DEX_AUTH_APP_NAME}:oidc-client"
    )

    await ops_test.model.integrate(
        f"{OIDC_GATEKEEPER.charm}:istio-ingress-route-unauthenticated",
        f"{ISTIO_INGRESS_K8S}:istio-ingress-route-unauthenticated",
    )

    await ops_test.model.integrate(
        f"{OIDC_GATEKEEPER.charm}:forward-auth",
        f"{ISTIO_INGRESS_K8S}:forward-auth",
    )

    # Deploy a sample app, to test the login process with
    await ops_test.model.deploy(
        KUBEFLOW_PROFILES.charm,
        channel=KUBEFLOW_PROFILES.channel,
        trust=KUBEFLOW_PROFILES.trust,
    )
    await ops_test.model.deploy(
        KUBEFLOW_DASHBOARD.charm,
        channel=KUBEFLOW_DASHBOARD.channel,
        trust=KUBEFLOW_DASHBOARD.trust,
    )
    await ops_test.model.integrate(
        f"{KUBEFLOW_PROFILES.charm}:kubeflow-profiles",
        f"{KUBEFLOW_DASHBOARD.charm}:kubeflow-profiles",
    )

    await integrate_with_service_mesh(KUBEFLOW_DASHBOARD.charm, ops_test.model)

    await ops_test.model.wait_for_idle(
        apps=[
            DEX_AUTH_APP_NAME,
            OIDC_GATEKEEPER.charm,
            KUBEFLOW_DASHBOARD.charm,
            KUBEFLOW_PROFILES.charm,
        ],
        status="active",
        raise_on_blocked=False,
        raise_on_error=True,
        timeout=600,
    )


@pytest.fixture()
async def driver(ops_test: OpsTest):
    ingress_ip = get_ingress_external_ip(ops_test.model.name)
    public_url = f"http://{ingress_ip}/"

    log.info("Ensuring Dex and AuthService are active.")
    await ops_test.model.wait_for_idle(
        [DEX_AUTH_APP_NAME, OIDC_GATEKEEPER.charm],
        status="active",
        raise_on_blocked=False,
        raise_on_error=False,
        timeout=600,
    )

    options = Options()
    options.headless = True

    with webdriver.Chrome(options=options) as driver:
        log.info("Deleting all the cookies.")
        driver.delete_all_cookies()

        wait = WebDriverWait(driver, 180, 1, (JavascriptException, StopIteration))
        for _ in range(60):
            try:
                log.info("Fetching the url: %s", public_url)
                driver.get(public_url)
                break
            except WebDriverException as exc:
                log.warn(exc)
                sleep(5)
        else:
            driver.get(public_url)

        log.info("Initialised the driver session info.")
        yield driver, wait, public_url

        driver.get_screenshot_as_file("/tmp/selenium-dashboard.png")


def fix_queryselector(elems):
    selectors = '").shadowRoot.querySelector("'.join(elems)
    return 'return document.querySelector("' + selectors + '")'


def test_login(driver):
    driver, wait, url = driver

    driver.get_screenshot_as_file("/tmp/selenium-logon.png")

    log.info("Logging in using dex credentials.")
    driver.find_element(By.ID, "login").send_keys(DEX_AUTH_CONFIG["static-username"])
    driver.find_element(By.ID, "password").send_keys(DEX_AUTH_CONFIG["static-password"])
    driver.find_element(By.ID, "submit-login").click()

    log.info("Checking if main page was loaded after the login.")
    script = fix_queryselector(["main-page", "dashboard-view", "#Quick-Links"])
    wait.until(lambda x: x.execute_script(script))


async def test_alert_rules(ops_test):
    """Test check charm alert rules and rules defined in relation data bag."""
    app = ops_test.model.applications[DEX_AUTH_APP_NAME]
    alert_rules = get_alert_rules()
    log.info("found alert_rules: %s", alert_rules)
    await assert_alert_rules(app, alert_rules)


async def test_metrics_enpoint(ops_test):
    """Test metrics_endpoints are defined in relation data bag and their accessibility.

    This function gets all the metrics_endpoints from the relation data bag, checks if
    they are available from the grafana-agent-k8s charm and finally compares them with the
    ones provided to the function.
    """
    # Set model-on-mesh to true temporarily to include grafana-agent-k8s in the service mesh
    # and be able to access the metrics endpoint since it can't relate to be beacon.
    # NOTE: This is a workaround until we replace grafana-agent-k8s with otel-collector-k8s
    # See https://github.com/canonical/charmed-kubeflow-chisme/issues/182.
    await ops_test.model.applications["istio-beacon-k8s"].set_config({"model-on-mesh": "true"})
    await ops_test.model.wait_for_idle(
        apps=["istio-beacon-k8s"],
        raise_on_blocked=False,
        raise_on_error=False,
        timeout=90 * 20,
    )

    app = ops_test.model.applications[DEX_AUTH_APP_NAME]
    await assert_metrics_endpoint(app, metrics_port=5558, metrics_path="/metrics")

    # revert model mesh to original state
    await ops_test.model.applications["istio-beacon-k8s"].set_config({"model-on-mesh": "false"})


async def test_logging(ops_test):
    """Test logging is defined in relation data bag."""
    app = ops_test.model.applications[GRAFANA_AGENT_APP]
    await assert_logging(app)


# Helper to retry calling a function over 30 seconds or 5 attempts
retry_for_5_attempts = Retrying(
    stop=(stop_after_attempt(5) | stop_after_delay(30)),
    wait=wait_exponential(multiplier=1, min=5, max=10),
    reraise=True,
)


@pytest.mark.parametrize("container_name", list(CONTAINERS_SECURITY_CONTEXT_MAP.keys()))
async def test_container_security_context(
    ops_test: OpsTest,
    lightkube_client: lightkube.Client,
    container_name: str,
):
    """Test container security context is correctly set.

    Verify that container spec defines the security context with correct
    user ID and group ID.
    """
    pod_name = get_pod_names(ops_test.model.name, DEX_AUTH_APP_NAME)[0]
    assert_security_context(
        lightkube_client,
        pod_name,
        container_name,
        CONTAINERS_SECURITY_CONTEXT_MAP,
        ops_test.model.name,
    )
