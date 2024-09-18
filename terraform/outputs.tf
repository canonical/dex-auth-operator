output "app_name" {
  value = juju_application.dex_auth.name
}

output "provides" {
  value = {
    dex_oidc_config   = "dex-oidc-config",
    grafana_dashboard = "grafana-dashboard",
    metrics_endpoint  = "metrics-endpoint",
  }
}

output "requires" {
  value = {
    ingress     = "ingress",
    oidc_client = "oidc-client",
  }
}
