output "app_name" {
  value = juju_application.dex_auth.name
}

output "provides" {
  value = {
    dex_oidc_config   = "dex-oidc-config",
    grafana_dashboard = "grafana-dashboard",
    metrics_endpoint  = "metrics-endpoint",
    provide_cmr_mesh  = "provide-cmr-mesh"
  }
}

output "requires" {
  value = {
    ingress                             = "ingress",
    istio_ingress_route_unauthenticated = "istio-ingress-route-unauthenticated",
    logging                             = "logging",
    oidc_client                         = "oidc-client",
    require_cmr_mesh                    = "require-cmr-mesh",
    service_mesh                        = "service-mesh"
  }
}
