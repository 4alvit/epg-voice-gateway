# All provider operations are mocked. No account or tunnel is contacted.
mock_provider "cloudflare" {}
variables {
  account_id        = "00000000000000000000000000000000"
  tunnel_id         = "00000000-0000-0000-0000-000000000001"
  workers_namespace = "fixture"
  worker_name       = "voice"
}
override_resource {
  target = cloudflare_worker.alexa
  values = {
    id        = "synthetic-worker"
    subdomain = { url = "voice.fixture.workers.dev", enabled = false, previews_enabled = false }
  }
}
override_resource {
  target = cloudflare_worker_version.alexa
  values = { id = "synthetic-version" }
}
override_resource {
  target = cloudflare_connectivity_directory_service.alexa
  values = { service_id = "synthetic-service" }
}
run "private_default" {
  command = plan
  assert {
    condition     = var.publish == false && cloudflare_worker.alexa.subdomain.enabled == false && cloudflare_worker.alexa.subdomain.previews_enabled == false
    error_message = "Workers must remain unpublished by default."
  }
  assert {
    condition     = !cloudflare_worker.alexa.logpush && !cloudflare_worker.alexa.observability.enabled && !cloudflare_worker.alexa.observability.logs.enabled && !cloudflare_worker.alexa.observability.traces.enabled && length(cloudflare_worker.alexa.tail_consumers) == 0
    error_message = "Logs, traces and tail consumers must remain disabled."
  }
  assert {
    condition     = cloudflare_connectivity_directory_service.alexa.host.ipv4 == "127.0.0.1" && cloudflare_connectivity_directory_service.alexa.http_port == 8080 && cloudflare_connectivity_directory_service.alexa.host.network.tunnel_id == var.tunnel_id
    error_message = "VPC must bind only the selected gateway address and port."
  }
  assert {
    condition     = cloudflare_worker_version.alexa.bindings[0].service_id == "synthetic-service" && cloudflare_worker_version.alexa.bindings[1].text == "voice.fixture.workers.dev" && cloudflare_worker_version.alexa.modules[0].content_base64 == filebase64("${path.module}/../relay.mjs")
    error_message = "The exact relay and selected VPC/host must be deployed together."
  }
  assert {
    condition     = cloudflare_workers_deployment.alexa.strategy == "percentage" && length(cloudflare_workers_deployment.alexa.versions) == 1 && cloudflare_workers_deployment.alexa.versions[0].version_id == "synthetic-version" && cloudflare_workers_deployment.alexa.versions[0].percentage == 100
    error_message = "Deployment must refer only to the selected version."
  }
}
run "reject_wrong_namespace" {
  command = plan
  override_resource {
    target = cloudflare_worker.alexa
    values = { subdomain = { url = "voice.wrong.workers.dev", enabled = false, previews_enabled = false } }
  }
  expect_failures = [cloudflare_worker.alexa]
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
    condition     = cloudflare_worker.alexa.subdomain.enabled == true && cloudflare_worker.alexa.subdomain.previews_enabled == false
    error_message = "Only explicit publication may enable the production route; previews stay disabled."
  }
}
