variable "region" {
  description = "AWS region for every environment."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Short name used in resource names."
  type        = string
  default     = "schedule-saas"
}

variable "github_repository" {
  description = "GitHub repository (owner/name) allowed to deploy."
  type        = string
  default     = "Acube-Innovation/SAAS-Admin"
}

variable "deploy_environments" {
  description = "GitHub environments that get a deploy role (one role each)."
  type        = list(string)
  default     = ["staging"]
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub Actions OIDC provider. Set false if the account already has one."
  type        = bool
  default     = true
}
