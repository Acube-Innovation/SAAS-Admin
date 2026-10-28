variable "region" {
  description = "AWS region."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Short name used in resource names."
  type        = string
  default     = "schedule-saas"
}

variable "environment" {
  description = "Environment name."
  type        = string
  default     = "staging"
}

variable "admin_domain" {
  description = "Host name of the super admin console, e.g. admin.staging.example.com. The API is served from the same host under /api."
  type        = string
}

variable "portal_domain" {
  description = "Host name of the client portal (used in invitation links; the portal is deployed in a later step)."
  type        = string
  default     = ""
}

variable "email_domain" {
  description = "Domain verified in SES for outgoing email (no-reply@<email_domain>)."
  type        = string
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone for the DNS records, or empty to add them at another DNS provider."
  type        = string
  default     = ""
}

variable "github_deploy_role_name" {
  description = "IAM role GitHub Actions deploys with (bootstrap output). Empty to skip attaching deploy permissions."
  type        = string
  default     = ""
}

variable "vpc_cidr" {
  description = "VPC address range."
  type        = string
  default     = "10.20.0.0/16"
}

variable "nat_gateway_per_az" {
  description = "One NAT gateway per availability zone (resilient, costs more). Staging uses one."
  type        = bool
  default     = false
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "Initial RDS storage in GiB (grows automatically up to 5x)."
  type        = number
  default     = 20
}

variable "db_multi_az" {
  description = "Standby replica in a second zone."
  type        = bool
  default     = false
}

variable "redis_node_type" {
  description = "ElastiCache node type."
  type        = string
  default     = "cache.t4g.micro"
}

variable "deletion_protection" {
  description = "Protect the database and load balancer from deletion."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch log retention for every service."
  type        = number
  default     = 30
}

variable "initial_image_tag" {
  description = "Image tag for the first task definitions. CI replaces it with each commit's tag."
  type        = string
  default     = "bootstrap"
}

variable "api_desired_count" {
  description = "API tasks."
  type        = number
  default     = 1
}

variable "worker_desired_count" {
  description = "Celery worker tasks."
  type        = number
  default     = 1
}

variable "admin_desired_count" {
  description = "Admin console tasks."
  type        = number
  default     = 1
}
