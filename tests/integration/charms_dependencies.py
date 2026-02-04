"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

OIDC_GATEKEEPER = CharmSpec(
    charm="oidc-gatekeeper",
    channel="ckf-1.10/stable",
    config={
        "client-name": "Ambassador Auth OIDC",
        "client-secret": "oidc-client-secret",
    },
    trust=True,
)
ISTIO_GATEWAY = CharmSpec(
    charm="istio-gateway", channel="1.24/stable", trust=True, config={"kind": "ingress"}
)
ISTIO_PILOT = CharmSpec(
    charm="istio-pilot",
    channel="1.24/stable",
    trust=True,
    config={"default-gateway": "kubeflow-gateway"},
)
KUBEFLOW_DASHBOARD = CharmSpec(charm="kubeflow-dashboard", channel="1.10/stable", trust=True)
KUBEFLOW_PROFILES = CharmSpec(charm="kubeflow-profiles", channel="1.10/stable", trust=True)
