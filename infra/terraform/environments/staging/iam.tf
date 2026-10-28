data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# --- execution role: pull images, read secrets, write logs (used by ECS, not the app) ------

resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app.arn, local.db_admin]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "read-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# --- task roles: what the running application may do ---------------------------------------

data "aws_iam_policy_document" "ecs_exec" {
  statement {
    sid = "EcsExec"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "backend" {
  source_policy_documents = [data.aws_iam_policy_document.ecs_exec.json]
  statement {
    sid       = "Files"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.files.arn}/*"]
  }
  statement {
    sid       = "FilesList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.files.arn]
  }
  statement {
    sid       = "Email"
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = [aws_sesv2_email_identity.main.arn, aws_sesv2_configuration_set.main.arn]
  }
}

resource "aws_iam_role" "backend" {
  name               = "${local.name}-backend"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "backend" {
  name   = "app"
  role   = aws_iam_role.backend.id
  policy = data.aws_iam_policy_document.backend.json
}

resource "aws_iam_role" "admin" {
  name               = "${local.name}-admin"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "admin" {
  name   = "ecs-exec"
  role   = aws_iam_role.admin.id
  policy = data.aws_iam_policy_document.ecs_exec.json
}

# --- GitHub Actions: register task definitions, run migrations, roll out services ----------

locals {
  service_arns = [
    for service in [module.api, module.worker, module.beat, module.admin] :
    "arn:aws:ecs:${var.region}:${local.account_id}:service/${aws_ecs_cluster.main.name}/${service.service_name}"
  ]
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "TaskDefinitions"
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"]
    resources = ["*"] # these actions do not support resource-level permissions
  }
  statement {
    sid       = "Migrate"
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${module.migrate.family}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.main.arn]
    }
  }
  statement {
    sid       = "WatchTasks"
    actions   = ["ecs:DescribeTasks"]
    resources = ["arn:aws:ecs:${var.region}:${local.account_id}:task/${aws_ecs_cluster.main.name}/*"]
  }
  statement {
    sid       = "RollOut"
    actions   = ["ecs:DescribeServices", "ecs:UpdateService"]
    resources = local.service_arns
  }
  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.execution.arn, aws_iam_role.backend.arn, aws_iam_role.admin.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid       = "MigrationLogs"
    actions   = ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:${module.migrate.log_group}:*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  count  = var.github_deploy_role_name == "" ? 0 : 1
  name   = "ecs-deploy-${var.environment}"
  role   = var.github_deploy_role_name
  policy = data.aws_iam_policy_document.deploy.json
}
