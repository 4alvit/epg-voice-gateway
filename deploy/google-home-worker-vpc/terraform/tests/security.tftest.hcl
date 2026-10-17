# All provider operations are mocked. No account or tunnel is contacted.
mock_provider "cloudflare" {}
variables {
  account_id        = "00000000000000000000000000000000"
  tunnel_id         = "00000000-0000-0000-0000-000000000001"
  workers_namespace = "fixture"
  worker_name       = "voice"
}
override_resource {
  target = cloudflare_worker.google
  values = {
    id        = "synthetic-worker"
    subdomain = { url = "voice.fixture.workers.dev", enabled = false, previews_enabled = false }
  }
}
override_resource {
  target = cloudflare_worker_version.google
  values = { id = "synthetic-version" }
}
override_resource {
  target = cloudflare_connectivity_directory_service.google
  values = { service_id = "synthetic-service" }
}
run "private_default" {
  command = plan
  assert {
    condition     = var.publish == false && cloudflare_worker.google.subdomain.enabled == false && cloudflare_worker.google.subdomain.previews_enabled == false
    error_message = "Workers must remain unpublished by default."
  }
  assert {
    condition     = !cloudflare_worker.google.logpush && !cloudflare_worker.google.observability.enabled && !cloudflare_worker.google.observability.logs.enabled && !cloudflare_worker.google.observability.traces.enabled && length(cloudflare_worker.google.tail_consumers) == 0
    error_message = "Logs, traces and tail consumers must remain disabled."
  }
  assert {
    condition     = cloudflare_connectivity_directory_service.google.host.ipv4 == "127.0.0.1" && cloudflare_connectivity_directory_service.google.http_port == 8123 && cloudflare_connectivity_directory_service.google.host.network.tunnel_id == var.tunnel_id
    error_message = "VPC must bind only the selected gateway address and port."
  }
  assert {
    condition     = cloudflare_worker_version.google.bindings[0].service_id == "synthetic-service" && cloudflare_worker_version.google.bindings[1].text == "voice.fixture.workers.dev" && cloudflare_worker_version.google.modules[0].content_base64 == filebase64("${path.module}/../relay.mjs")
    error_message = "The exact relay and selected VPC/host must be deployed together."
  }
  assert {
    condition     = cloudflare_workers_deployment.google.strategy == "percentage" && length(cloudflare_workers_deployment.google.versions) == 1 && cloudflare_workers_deployment.google.versions[0].version_id == "synthetic-version" && cloudflare_workers_deployment.google.versions[0].percentage == 100
    error_message = "Deployment must refer only to the selected version."
  }
}
run "reject_wrong_namespace" {
  command = plan
  override_resource {
    target = cloudflare_worker.google
    values = { subdomain = { url = "voice.wrong.workers.dev", enabled = false, previews_enabled = false } }
  }
  expect_failures = [cloudflare_worker.google]
}
run "reject_invalid_account" {
  command = plan
  variables { account_id = "not-an-account" }
  expect_failures = [var.account_id]
}

run "explicit_publish_only" {
  command = plan
  variables { publish = true }
  assert {
    condition     = cloudflare_worker.google.subdomain.enabled == true && cloudflare_worker.google.subdomain.previews_enabled == false
    error_message = "Only explicit publication may enable the production route; previews stay disabled."
  }
}

run "explicit_channels_only" {
  command = plan
  variables { channels_enabled = true }
  assert {
    condition     = cloudflare_worker_version.google.bindings[2].name == "CHANNELS_ENABLED" && cloudflare_worker_version.google.bindings[2].text == "true"
    error_message = "The selected channel mode must reach the original relay binding."
  }
}
