locals {
  worker_hostname = "${var.worker_name}.${var.workers_namespace}.workers.dev"
  relay_source    = "${path.module}/../relay.mjs"
}

# A single service grants access to one loopback port, not to a private network.
# No public tunnel ingress, DNS, Access policy, or zone resource is managed here.
resource "cloudflare_connectivity_directory_service" "google" {
  account_id = var.account_id
  name       = "${var.worker_name}-backend"
  type       = "http"
  http_port  = var.backend_port
  host = {
    ipv4 = "127.0.0.1"
    network = {
      tunnel_id = var.tunnel_id
    }
  }
}

resource "cloudflare_worker" "google" {
  account_id     = var.account_id
  name           = var.worker_name
  logpush        = false
  tail_consumers = []
  subdomain = {
    enabled          = var.publish
    previews_enabled = false
  }
  # The API normalizes tuning defaults for disabled features. Keep those
  # provider-computed to avoid perpetual drift while explicitly disabling logs.
  observability = {
    enabled = false
    logs = {
      enabled = false
    }
    traces = {
      enabled = false
    }
  }

  lifecycle {
    postcondition {
      condition = try(contains([
        local.worker_hostname,
        "https://${local.worker_hostname}",
      ], self.subdomain.url), false)
      error_message = "The account must already own the exact workers_namespace. Stop and inspect the namespace; this module never registers one. Keep publish=false until verification succeeds."
    }
  }
}

resource "cloudflare_worker_version" "google" {
  account_id         = var.account_id
  worker_id          = cloudflare_worker.google.id
  compatibility_date = "2026-09-18"
  main_module        = "relay.mjs"
  modules = [{
    name           = "relay.mjs"
    content_type   = "application/javascript+module"
    content_base64 = filebase64(local.relay_source)
  }]
  bindings = [
    {
      name       = "BACKEND"
      type       = "vpc_service"
      service_id = cloudflare_connectivity_directory_service.google.service_id
    },
    {
      name = "PUBLIC_HOST"
      type = "plain_text"
      text = local.worker_hostname
    },
  ]

  lifecycle {
    create_before_destroy = true
  }
}

resource "cloudflare_workers_deployment" "google" {
  account_id  = var.account_id
  script_name = cloudflare_worker.google.name
  strategy    = "percentage"
  versions = [{
    version_id = cloudflare_worker_version.google.id
    percentage = 100
  }]
}
