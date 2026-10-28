output "state_bucket" {
  description = "S3 bucket for the environment stacks' Terraform state (backend.hcl: bucket)."
  value       = aws_s3_bucket.state.bucket
}

output "ecr_repositories" {
  description = "Image repository URLs."
  value       = { for key, repo in aws_ecr_repository.this : key => repo.repository_url }
}

output "github_deploy_roles" {
  description = "Deploy role per GitHub environment: name (Terraform variable) and ARN (GitHub variable)."
  value       = { for env, role in aws_iam_role.github_deploy : env => { name = role.name, arn = role.arn } }
}
