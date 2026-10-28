resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

data "aws_ecr_repository" "api" {
  name = "${var.project}/api"
}

data "aws_ecr_repository" "admin" {
  name = "${var.project}/admin"
}

locals {
  api_image   = "${data.aws_ecr_repository.api.repository_url}:${var.initial_image_tag}"
  admin_image = "${data.aws_ecr_repository.admin.repository_url}:${var.initial_image_tag}"
  app_secret  = aws_secretsmanager_secret.app.arn
  db_admin    = aws_db_instance.main.master_user_secret[0].secret_arn

  backend_environment = {
    DJANGO_SETTINGS_MODULE      = "config.settings.staging"
    DJANGO_ALLOWED_HOSTS        = var.admin_domain
    DJANGO_CSRF_TRUSTED_ORIGINS = "https://${var.admin_domain}"
    ADMIN_BASE_URL              = "https://${var.admin_domain}"
    PORTAL_BASE_URL             = var.portal_domain == "" ? "" : "https://${var.portal_domain}"
    POSTGRES_HOST               = aws_db_instance.main.address
    POSTGRES_PORT               = tostring(aws_db_instance.main.port)
    POSTGRES_DB                 = aws_db_instance.main.db_name
    POSTGRES_OWNER_USER         = "app_owner"
    POSTGRES_APP_USER           = "app_user"
    POSTGRES_SSLMODE            = "require"
    AWS_STORAGE_BUCKET_NAME     = aws_s3_bucket.files.bucket
    AWS_S3_REGION_NAME          = var.region
    EMAIL_BACKEND               = "apps.core.email.SesEmailBackend"
    AWS_SES_REGION_NAME         = var.region
    AWS_SES_CONFIGURATION_SET   = aws_sesv2_configuration_set.main.configuration_set_name
    DEFAULT_FROM_EMAIL          = "Schedule Confirmation <no-reply@${var.email_domain}>"
    WHATSAPP_DEV_CONTROLS       = "false"
    LOG_LEVEL                   = "INFO"
  }
  # The API and workers connect as app_user (cannot bypass Row-Level Security).
  backend_secrets = {
    DJANGO_SECRET_KEY     = "${local.app_secret}:DJANGO_SECRET_KEY::"
    POSTGRES_APP_PASSWORD = "${local.app_secret}:POSTGRES_APP_PASSWORD::"
    REDIS_URL             = "${local.app_secret}:REDIS_URL::"
  }
  service_defaults = {
    cluster_arn        = aws_ecs_cluster.main.arn
    execution_role_arn = aws_iam_role.execution.arn
    region             = var.region
    log_retention_days = var.log_retention_days
    subnet_ids         = aws_subnet.private[*].id
  }
}

module "api" {
  source                 = "../../modules/ecs_service"
  name                   = "${local.name}-api"
  image                  = local.api_image
  cpu                    = 512
  memory                 = 1024
  port                   = 8000
  environment            = local.backend_environment
  secrets                = local.backend_secrets
  container_health_check = "curl -fsS http://localhost:8000/api/health || exit 1"
  task_role_arn          = aws_iam_role.backend.arn
  desired_count          = var.api_desired_count
  security_group_ids     = [aws_security_group.api.id]
  target_group_arn       = aws_lb_target_group.api.arn
  cluster_arn            = local.service_defaults.cluster_arn
  execution_role_arn     = local.service_defaults.execution_role_arn
  region                 = local.service_defaults.region
  log_retention_days     = local.service_defaults.log_retention_days
  subnet_ids             = local.service_defaults.subnet_ids
  depends_on             = [aws_lb_listener_rule.api]
}

module "worker" {
  source             = "../../modules/ecs_service"
  name               = "${local.name}-worker"
  image              = local.api_image
  command            = ["celery", "-A", "config", "worker", "--loglevel=info", "--concurrency=2"]
  cpu                = 512
  memory             = 1024
  environment        = local.backend_environment
  secrets            = local.backend_secrets
  task_role_arn      = aws_iam_role.backend.arn
  desired_count      = var.worker_desired_count
  security_group_ids = [aws_security_group.tasks.id]
  cluster_arn        = local.service_defaults.cluster_arn
  execution_role_arn = local.service_defaults.execution_role_arn
  region             = local.service_defaults.region
  log_retention_days = local.service_defaults.log_retention_days
  subnet_ids         = local.service_defaults.subnet_ids
}

# Exactly one scheduler (support-session expiry, template status sync).
module "beat" {
  source             = "../../modules/ecs_service"
  name               = "${local.name}-beat"
  image              = local.api_image
  command            = ["celery", "-A", "config", "beat", "--loglevel=info", "--schedule", "/tmp/celerybeat-schedule"]
  cpu                = 256
  memory             = 512
  environment        = local.backend_environment
  secrets            = local.backend_secrets
  task_role_arn      = aws_iam_role.backend.arn
  desired_count      = 1
  singleton          = true
  security_group_ids = [aws_security_group.tasks.id]
  cluster_arn        = local.service_defaults.cluster_arn
  execution_role_arn = local.service_defaults.execution_role_arn
  region             = local.service_defaults.region
  log_retention_days = local.service_defaults.log_retention_days
  subnet_ids         = local.service_defaults.subnet_ids
}

module "admin" {
  source             = "../../modules/ecs_service"
  name               = "${local.name}-admin"
  image              = local.admin_image
  cpu                = 256
  memory             = 512
  port               = 3000
  environment        = { NODE_ENV = "production" }
  task_role_arn      = aws_iam_role.admin.arn
  desired_count      = var.admin_desired_count
  security_group_ids = [aws_security_group.admin.id]
  target_group_arn   = aws_lb_target_group.admin.arn
  cluster_arn        = local.service_defaults.cluster_arn
  execution_role_arn = local.service_defaults.execution_role_arn
  region             = local.service_defaults.region
  log_retention_days = local.service_defaults.log_retention_days
  subnet_ids         = local.service_defaults.subnet_ids
  depends_on         = [aws_lb_listener.https]
}

# One-off task run by CI before each rollout: create/update the DB roles, then migrate as app_owner.
module "migrate" {
  source  = "../../modules/ecs_service"
  name    = "${local.name}-migrate"
  image   = local.api_image
  command = ["sh", "-c", "python manage.py ensure_db_roles && python manage.py migrate --noinput"]
  cpu     = 256
  memory  = 512
  environment = merge(local.backend_environment, {
    DJANGO_DB_ROLE = "owner"
  })
  secrets = merge(local.backend_secrets, {
    POSTGRES_OWNER_PASSWORD = "${local.app_secret}:POSTGRES_OWNER_PASSWORD::"
    POSTGRES_ADMIN_USER     = "${local.db_admin}:username::"
    POSTGRES_ADMIN_PASSWORD = "${local.db_admin}:password::"
  })
  task_role_arn          = aws_iam_role.backend.arn
  create_service         = false
  enable_execute_command = false
  cluster_arn            = local.service_defaults.cluster_arn
  execution_role_arn     = local.service_defaults.execution_role_arn
  region                 = local.service_defaults.region
  log_retention_days     = local.service_defaults.log_retention_days
}
