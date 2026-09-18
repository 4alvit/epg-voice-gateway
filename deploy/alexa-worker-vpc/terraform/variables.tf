variable "account_id" {
  description = "Existing Cloudflare account that owns the tunnel and workers.dev namespace."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.account_id))
    error_message = "account_id must contain 32 lowercase hexadecimal characters."
  }
}

variable "tunnel_id" {
  description = "Existing tunnel UUID. Every connector must reach the same intended gateway target."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.tunnel_id))
    error_message = "tunnel_id must be the existing tunnel's lowercase UUID."
  }
}

variable "workers_namespace" {
  description = "Existing account workers.dev subdomain label. This module never registers or changes it."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.workers_namespace))
    error_message = "workers_namespace must be one lowercase DNS label with at most 63 characters."
  }
}

variable "worker_name" {
  description = "Unused dedicated Worker name. Never select an unrelated existing Worker."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.worker_name))
    error_message = "worker_name must be one lowercase DNS label with at most 63 characters."
  }
}

variable "backend_ipv4" {
  description = "Fixed gateway IPv4 address as reached from the tunnel connector network namespace. Use loopback only when that namespace actually contains the gateway or a verified forwarder."
  type        = string
  default     = "127.0.0.1"
  nullable    = false

  validation {
    condition     = can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.backend_ipv4)) && can(cidrnetmask("${var.backend_ipv4}/32"))
    error_message = "backend_ipv4 must be one IPv4 address, without a port, prefix, or URL."
  }
}

variable "backend_port" {
  description = "Fixed HTTP port of the private EPG Voice Gateway. Its standard container port is 8080."
  type        = number
  default     = 8080
  nullable    = false

  validation {
    condition     = var.backend_port == floor(var.backend_port) && var.backend_port >= 1 && var.backend_port <= 65535
    error_message = "backend_port must be an integer from 1 through 65535."
  }
}

variable "publish" {
  description = "Enable the dedicated production workers.dev address only after a disabled deployment and backend verification."
  type        = bool
  default     = false
  nullable    = false
}
