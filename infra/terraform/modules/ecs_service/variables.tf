variable "name" {
  description = "Task definition family and service name, e.g. schedule-saas-staging-api."
  type        = string
}

variable "cluster_arn" {
  description = "ECS cluster ARN."
  type        = string
}

variable "image" {
  description = "Container image for the first revision. CI registers later revisions with each commit's image."
  type        = string
}

variable "command" {
  description = "Container command; null keeps the image's CMD."
  type        = list(string)
  default     = null
}

variable "cpu" {
  description = "Task CPU units (256 = 0.25 vCPU)."
  type        = number
}

variable "memory" {
  description = "Task memory in MiB."
  type        = number
}

variable "port" {
  description = "Container port, or null for services without one (workers)."
  type        = number
  default     = null
}

variable "environment" {
  description = "Plain environment variables."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Environment variables read from Secrets Manager at start: name => valueFrom ARN (with :json-key::)."
  type        = map(string)
  default     = {}
}

variable "container_health_check" {
  description = "Command for the container health check (CMD-SHELL), or null."
  type        = string
  default     = null
}

variable "execution_role_arn" {
  description = "Role ECS uses to pull the image, read secrets and write logs."
  type        = string
}

variable "task_role_arn" {
  description = "Role the application runs as."
  type        = string
}

variable "region" {
  description = "AWS region (for the log driver)."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}

variable "create_service" {
  description = "False for one-off tasks (migrations) that only need a task definition."
  type        = bool
  default     = true
}

variable "desired_count" {
  description = "Number of tasks."
  type        = number
  default     = 1
}

variable "singleton" {
  description = "Never run two copies, even during a deploy (Celery beat)."
  type        = bool
  default     = false
}

variable "subnet_ids" {
  description = "Private subnets for the tasks."
  type        = list(string)
  default     = []
}

variable "security_group_ids" {
  description = "Security groups for the tasks."
  type        = list(string)
  default     = []
}

variable "target_group_arn" {
  description = "Load balancer target group, or null."
  type        = string
  default     = null
}

variable "enable_execute_command" {
  description = "Allow `aws ecs execute-command` (one-off management commands)."
  type        = bool
  default     = true
}
