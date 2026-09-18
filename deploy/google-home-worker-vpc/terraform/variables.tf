variable "account_id" {
  description = "Cloudflare account that already owns the tunnel and a workers.dev namespace."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.account_id))
    error_message = "account_id must be the existing account's 32 lowercase hexadecimal characters."
  }
}

variable "tunnel_id" {
  description = "Existing tunnel UUID. Every connector must reach the same backend at its own loopback address."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.tunnel_id))
    error_message = "tunnel_id must be the existing tunnel's lowercase UUID."
  }
}

variable "workers_namespace" {
  description = "Existing account workers.dev subdomain label, without .workers.dev. This module never creates or changes it."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.workers_namespace))
    error_message = "workers_namespace must be one lowercase DNS label with at most 63 characters."
  }
}

variable "worker_name" {
  description = "Unique dedicated Worker name. Do not choose the name of an unrelated existing Worker."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.worker_name))
    error_message = "worker_name must be one lowercase DNS label with at most 63 characters."
  }
}

variable "backend_port" {
  description = "HTTP port on the tunnel connector's loopback interface for the verified Home Assistant."
  type        = number
  default     = 8123
  nullable    = false

  validation {
    condition     = var.backend_port == floor(var.backend_port) && var.backend_port >= 1024 && var.backend_port <= 65535
    error_message = "backend_port must be an integer from 1024 through 65535."
  }
}

variable "publish" {
  description = "Enable this Worker's production workers.dev address only after the initial disabled deployment and backend verification."
  type        = bool
  default     = false
  nullable    = false
}
