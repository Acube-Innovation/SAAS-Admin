terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # Local state on purpose: this stack creates the bucket that every other stack keeps its state in.
  # Keep bootstrap/terraform.tfstate safe (it holds no secrets) or import the resources if lost.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Stack     = "bootstrap"
    }
  }
}
