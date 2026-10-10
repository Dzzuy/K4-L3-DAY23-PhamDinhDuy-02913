terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  alias  = "source"
  region = var.source_region
}

provider "aws" {
  alias  = "destination"
  region = var.destination_region
}
