terraform {
  required_version = ">= 1.7.0, < 2.0.0"

  required_providers {
    cloudflare = {
      source  = "registry.terraform.io/cloudflare/cloudflare"
      version = "= 5.24.0"
    }
  }
}

# Supply CLOUDFLARE_API_TOKEN through the operator's environment.
# Never put credentials or Home Assistant tokens in Terraform variables.
provider "cloudflare" {}
