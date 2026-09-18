output "token_url" {
  description = "Proposed token URL; usable only after publication and verification."
  value       = "https://${local.worker_hostname}/auth/token"
}

output "fulfillment_url" {
  description = "Proposed fulfillment URL; usable only after publication and verification."
  value       = "https://${local.worker_hostname}/api/google_assistant"
}

output "relay_sha256" {
  description = "Hash of the exact reviewed relay source."
  value       = filesha256(local.relay_source)
}
