output "family" {
  description = "Task definition family."
  value       = aws_ecs_task_definition.this.family
}

output "service_name" {
  description = "ECS service name, or null for task-only modules."
  value       = var.create_service ? aws_ecs_service.this[0].name : null
}

output "log_group" {
  description = "CloudWatch log group."
  value       = aws_cloudwatch_log_group.this.name
}

output "container" {
  description = "The container definition (environment, secret names, command), for checks and tests."
  value       = local.container
}
