output "alexa_url" {
  description = "Proposed Alexa HTTPS endpoint; usable only after authorized publication and verification."
  value       = "https://${local.worker_hostname}/alexa"
}

output "relay_sha256" {
  description = "Hash of the exact reviewed relay source."
  value       = filesha256(local.relay_source)
}
