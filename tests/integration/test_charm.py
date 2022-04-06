# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path

import pytest
import requests
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import (
    Retrying,
    RetryError,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

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
        raise_on_blocked=True,
        raise_on_error=True,
        timeout=600,
    )


async def test_prometheus_grafana_integration(ops_test: OpsTest):
    """Deploy prometheus, grafana and required relations, then test the metrics."""
    prometheus = "prometheus-k8s"
    grafana = "grafana-k8s"
    prometheus_scrape_charm = "prometheus-scrape-config-k8s"
    scrape_config = {"scrape_interval": "5s"}

    await ops_test.model.deploy(prometheus, channel="latest/beta")
    await ops_test.model.deploy(grafana, channel="latest/beta")
    await ops_test.model.add_relation(prometheus, grafana)
    await ops_test.model.add_relation(APP_NAME, grafana)
    await ops_test.model.deploy(
        prometheus_scrape_charm, channel="latest/beta", config=scrape_config
    )
    await ops_test.model.add_relation(APP_NAME, prometheus_scrape_charm)
    await ops_test.model.add_relation(prometheus, prometheus_scrape_charm)

    await ops_test.model.wait_for_idle(status="active", timeout=60 * 10)

    status = await ops_test.model.get_status()
    prometheus_unit_ip = status["applications"][prometheus]["units"][f"{prometheus}/0"][
        "address"
    ]
    log.info(f"Prometheus available at http://{prometheus_unit_ip}:9090")

    try:
        for attempt in Retrying(
            (stop_after_attempt(5) | stop_after_delay(30)),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        ):
            with attempt:
                r = requests.get(
                    f'http://{prometheus_unit_ip}:9090/api/v1/query?query=up{{juju_application="{APP_NAME}"}}'
                )
                response = json.loads(r.content.decode("utf-8"))
                response_status = response["status"]
                log.info(f"Response status is {response_status}")
                assert response_status == "success"

                response_metric = response["data"]["result"][0]["metric"]
                assert response_metric["juju_application"] == APP_NAME
                assert response_metric["juju_model"] == ops_test.model_name
    except RetryError:
        log.info("Testing prometheus failed")
