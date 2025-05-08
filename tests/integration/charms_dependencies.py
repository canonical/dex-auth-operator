"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

OIDC_GATEKEEPER = CharmSpec(
    charm="oidc-gatekeeper",
    channel="latest/edge",
    config={
        "client-name": "Ambassador Auth OIDC",
        "client-secret": "oidc-client-secret",
    },
    trust=True,
)
ISTIO_GATEWAY = CharmSpec(
    charm="istio-gateway", channel="latest/edge", trust=True, config={"kind": "ingress"}
)
ISTIO_PILOT = CharmSpec(
    charm="istio-pilot",
    channel="latest/edge",
    trust=True,
    config={"default-gateway": "kubeflow-gateway"},
)
KUBEFLOW_DASHBOARD = CharmSpec(charm="kubeflow-dashboard", channel="latest/edge", trust=True)
KUBEFLOW_PROFILES = CharmSpec(charm="kubeflow-profiles", channel="latest/edge", trust=True)
