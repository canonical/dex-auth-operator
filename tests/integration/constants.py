# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
"""Constants module including constants used in tests."""
from pathlib import Path

import yaml

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
KUBEFLOW_PROFILES = True

KUBEFLOW_DASHBOARD = "kubeflow-dashboard"
KUBEFLOW_DASHBOARD_CHANNEL = "1.8/stable"
KUBEFLOW_DASHBOARD = True

PROMETHEUS_K8S = "prometheus-k8s"
PROMETHEUS_K8S_CHANNEL = "1.0/stable"
PROMETHEUS_K8S_TRUST = True
GRAFANA_K8S = "grafana-k8s"
GRAFANA_K8S_CHANNEL = "1.0/stable"
GRAFANA_K8S_TRUST = True
PROMETHEUS_SCRAPE_K8S = "prometheus-scrape-config-k8s"
PROMETHEUS_SCRAPE_K8S_CHANNEL = "1.0/stable"
PROMETHEUS_SCRAPE_CONFIG = {"scrape_interval": "30s"}
