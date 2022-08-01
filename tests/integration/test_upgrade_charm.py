import json
import logging
from pathlib import Path

import pytest
import requests
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import (
    Retrying,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

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
    prometheus_unit_ip = status["applications"][prometheus]["units"][f"{prometheus}/0"][
        "address"
    ]
    log.info(f"Prometheus available at http://{prometheus_unit_ip}:9090")

    for attempt in retry_for_5_attempts:
        log.info(
            f"Testing prometheus deployment (attempt "
            f"{attempt.retry_state.attempt_number})"
        )
        with attempt:
            r = requests.get(
                f'http://{prometheus_unit_ip}:9090/api/v1/query?'
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
