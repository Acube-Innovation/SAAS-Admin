output "admin_url" {
  description = "Super admin console."
  value       = "https://${var.admin_domain}"
}

output "load_balancer_dns_name" {
  description = "Point admin_domain here (CNAME, or an alias A record) when not using Route 53."
  value       = aws_lb.main.dns_name
}

output "dns_records_to_add" {
  description = "Records to create at your DNS provider when route53_zone_id is empty."
  value = local.use_dns ? null : {
    certificate_validation = [
      for option in aws_acm_certificate.admin.domain_validation_options :
      { name = option.resource_record_name, type = option.resource_record_type, value = option.resource_record_value }
    ]
    admin = { name = var.admin_domain, type = "CNAME", value = aws_lb.main.dns_name }
    ses_dkim = [
      for token in aws_sesv2_email_identity.main.dkim_signing_attributes[0].tokens :
      { name = "${token}._domainkey.${var.email_domain}", type = "CNAME", value = "${token}.dkim.amazonses.com" }
    ]
  }
}

output "github_environment_variables" {
  description = "Set these as variables of the GitHub environment \"staging\" (Settings > Environments)."
  value = {
    AWS_REGION               = var.region
    ECS_CLUSTER              = aws_ecs_cluster.main.name
    ECS_NAME_PREFIX          = local.name
    ECS_PRIVATE_SUBNETS      = join(",", aws_subnet.private[*].id)
    ECS_TASKS_SECURITY_GROUP = aws_security_group.tasks.id
    ECR_API_REPOSITORY       = data.aws_ecr_repository.api.repository_url
    ECR_ADMIN_REPOSITORY     = data.aws_ecr_repository.admin.repository_url
    STAGING_URL              = "https://${var.admin_domain}"
  }
}

output "files_bucket" {
  description = "S3 bucket for uploaded files."
  value       = aws_s3_bucket.files.bucket
}

output "database_endpoint" {
  description = "RDS endpoint (private)."
  value       = aws_db_instance.main.address
}
