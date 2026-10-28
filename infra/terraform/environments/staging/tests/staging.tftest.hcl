# Offline checks of the staging stack with a mocked AWS provider (no credentials, nothing created):
#   terraform init -backend=false && terraform test

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["ap-south-1a", "ap-south-1b", "ap-south-1c"] }
  }
  mock_data "aws_ecr_repository" {
    defaults = { repository_url = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/schedule-saas/api" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:targetgroup/mock/0123456789abcdef" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:loadbalancer/app/mock/0123456789abcdef" }
  }
  mock_resource "aws_lb_listener" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:listener/app/mock/0123456789abcdef/0123456789abcdef" }
  }
  mock_resource "aws_acm_certificate" {
    defaults = { arn = "arn:aws:acm:ap-south-1:123456789012:certificate/mock" }
  }
  mock_resource "aws_acm_certificate_validation" {
    defaults = { certificate_arn = "arn:aws:acm:ap-south-1:123456789012:certificate/mock" }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { arn = "arn:aws:ecs:ap-south-1:123456789012:cluster/schedule-saas-staging" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:ap-south-1:123456789012:secret:schedule-saas-staging/app-AbCdEf" }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address            = "schedule-saas-staging-postgres.abc.ap-south-1.rds.amazonaws.com"
      port               = 5432
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:ap-south-1:123456789012:secret:rds!db-mock-AbCdEf", kms_key_id = "", secret_status = "active" }]
    }
  }
  mock_resource "aws_sesv2_email_identity" {
    defaults = {
      arn                     = "arn:aws:ses:ap-south-1:123456789012:identity/staging.example.com"
      dkim_signing_attributes = { tokens = ["tok1", "tok2", "tok3"] }
    }
  }
  mock_resource "aws_sesv2_configuration_set" {
    defaults = { arn = "arn:aws:ses:ap-south-1:123456789012:configuration-set/schedule-saas-staging" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::schedule-saas-staging-files-123456789012" }
  }
}

variables {
  admin_domain            = "admin.staging.example.com"
  email_domain            = "staging.example.com"
  portal_domain           = "portal.staging.example.com"
  github_deploy_role_name = "schedule-saas-github-deploy-staging"
}

run "routes_api_to_django_and_the_rest_to_the_console" {
  command = apply

  assert {
    condition     = one(one(aws_lb_listener_rule.api.condition).path_pattern).values == toset(["/api/*"])
    error_message = "Only /api/* goes to the API target group"
  }
  assert {
    condition     = aws_lb_listener_rule.api.action[0].target_group_arn == aws_lb_target_group.api.arn
    error_message = "/api/* must forward to the API"
  }
  assert {
    condition     = aws_lb_listener.https.default_action[0].target_group_arn == aws_lb_target_group.admin.arn
    error_message = "Everything else goes to the admin console"
  }
  assert {
    condition     = aws_lb_listener.http.default_action[0].redirect[0].protocol == "HTTPS"
    error_message = "HTTP must redirect to HTTPS"
  }
  assert {
    condition     = aws_lb_target_group.api.health_check[0].path == "/api/health"
    error_message = "API health check path"
  }
}

run "network_keeps_data_stores_private" {
  command = apply

  assert {
    condition     = length(aws_subnet.private) == 2 && length(aws_subnet.data) == 2
    error_message = "Two zones of private and data subnets"
  }
  assert {
    condition     = aws_db_instance.main.publicly_accessible == false && aws_db_instance.main.storage_encrypted
    error_message = "RDS is private and encrypted"
  }
  assert {
    condition     = aws_elasticache_replication_group.redis.transit_encryption_enabled && aws_elasticache_replication_group.redis.at_rest_encryption_enabled
    error_message = "Redis encrypted in transit and at rest"
  }
  assert {
    condition     = alltrue([for a in aws_route_table_association.data : a.route_table_id == aws_route_table.data.id])
    error_message = "Data subnets use the data route table, which has no internet route"
  }
  assert {
    condition     = length(aws_nat_gateway.main) == 1
    error_message = "Staging uses a single NAT gateway by default"
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.files.restrict_public_buckets
    error_message = "Files bucket blocks public access"
  }
}

run "app_containers_get_least_privilege_secrets" {
  command = apply

  assert {
    condition     = contains(module.api.container.environment, { name = "DJANGO_SETTINGS_MODULE", value = "config.settings.staging" })
    error_message = "API runs with staging settings"
  }
  assert {
    condition     = contains(module.api.container.environment, { name = "POSTGRES_SSLMODE", value = "require" })
    error_message = "Database connections use TLS"
  }
  assert {
    condition     = contains(module.worker.container.environment, { name = "WHATSAPP_DEV_CONTROLS", value = "false" })
    error_message = "Developer simulation controls are off"
  }
  assert {
    condition = alltrue([
      for service in [module.api, module.worker, module.beat] :
      sort([for s in service.container.secrets : s.name]) == tolist(["DJANGO_SECRET_KEY", "POSTGRES_APP_PASSWORD", "REDIS_URL"])
    ])
    error_message = "API and workers get only the app_user password: never the owner or RDS admin password"
  }
  assert {
    condition     = !contains([for e in module.api.container.environment : e.name], "DJANGO_DB_ROLE")
    error_message = "API connects as app_user (the default role)"
  }
  assert {
    condition     = length(module.admin.container.secrets) == 0
    error_message = "The admin console container needs no secrets"
  }
}

run "migrations_run_as_the_owner_role" {
  command = apply

  assert {
    condition     = contains(module.migrate.container.environment, { name = "DJANGO_DB_ROLE", value = "owner" })
    error_message = "Migrations connect as app_owner"
  }
  assert {
    condition = sort([for s in module.migrate.container.secrets : s.name]) == tolist([
      "DJANGO_SECRET_KEY", "POSTGRES_ADMIN_PASSWORD", "POSTGRES_ADMIN_USER", "POSTGRES_APP_PASSWORD",
      "POSTGRES_OWNER_PASSWORD", "REDIS_URL",
    ])
    error_message = "The migrate task gets the owner and RDS admin credentials"
  }
  assert {
    condition     = module.migrate.container.command[2] == "python manage.py ensure_db_roles && python manage.py migrate --noinput"
    error_message = "Roles first, then migrations"
  }
  assert {
    condition     = module.migrate.service_name == null
    error_message = "Migrate is a one-off task, not a service"
  }
  assert {
    condition     = one([for s in module.migrate.container.secrets : s.valueFrom if s.name == "POSTGRES_ADMIN_PASSWORD"]) == "arn:aws:secretsmanager:ap-south-1:123456789012:secret:rds!db-mock-AbCdEf:password::"
    error_message = "RDS admin password comes from the RDS-managed secret"
  }
}

run "beat_is_a_singleton" {
  command = apply

  assert {
    condition     = module.beat.container.command[3] == "beat"
    error_message = "Beat service runs celery beat"
  }
}

run "deploy_role_can_only_touch_this_environment" {
  command = apply

  assert {
    condition     = length(aws_iam_role_policy.deploy) == 1
    error_message = "Deploy permissions attach to the GitHub role"
  }
  assert {
    condition     = length(local.service_arns) == 4 && alltrue([for arn in local.service_arns : strcontains(arn, ":service/schedule-saas-staging/schedule-saas-staging-")])
    error_message = "Deploy role may update only the four staging services"
  }
}

run "dns_records_are_listed_when_route53_is_not_used" {
  command = apply

  assert {
    condition     = output.dns_records_to_add.admin.value == aws_lb.main.dns_name
    error_message = "Admin CNAME points at the load balancer"
  }
  assert {
    condition     = length(output.dns_records_to_add.ses_dkim) == 3
    error_message = "Three SES DKIM records"
  }
  assert {
    condition     = output.github_environment_variables.ECS_NAME_PREFIX == "schedule-saas-staging"
    error_message = "CI needs the ECS name prefix"
  }
}

run "route53_mode_creates_the_records" {
  command = apply
  variables {
    route53_zone_id = "Z0123456789ABCDEFGHIJ"
  }

  assert {
    condition     = length(aws_route53_record.admin) == 1 && length(aws_route53_record.ses_dkim) == 3
    error_message = "Admin alias and DKIM records are created"
  }
  assert {
    condition     = output.dns_records_to_add == null
    error_message = "Nothing to add by hand"
  }
}
