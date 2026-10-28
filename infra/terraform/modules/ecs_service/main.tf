resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

locals {
  container = merge(
    {
      name                   = "app"
      image                  = var.image
      essential              = true
      environment            = [for key in sort(keys(var.environment)) : { name = key, value = var.environment[key] }]
      secrets                = [for key in sort(keys(var.secrets)) : { name = key, valueFrom = var.secrets[key] }]
      linuxParameters        = { initProcessEnabled = true } # needed by ECS Exec
      readonlyRootFilesystem = false
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.this.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "app"
        }
      }
    },
    var.command == null ? {} : { command = var.command },
    var.port == null ? {} : { portMappings = [{ containerPort = var.port, protocol = "tcp" }] },
    var.container_health_check == null ? {} : {
      healthCheck = {
        command     = ["CMD-SHELL", var.container_health_check]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    },
  )
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn
  container_definitions    = jsonencode([local.container])

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
}

resource "aws_ecs_service" "this" {
  count                              = var.create_service ? 1 : 0
  name                               = var.name
  cluster                            = var.cluster_arn
  task_definition                    = aws_ecs_task_definition.this.arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  enable_execute_command             = var.enable_execute_command
  propagate_tags                     = "SERVICE"
  deployment_minimum_healthy_percent = var.singleton ? 0 : 100
  deployment_maximum_percent         = var.singleton ? 100 : 200
  health_check_grace_period_seconds  = var.target_group_arn == null ? null : 60

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  dynamic "load_balancer" {
    for_each = var.target_group_arn == null ? [] : [var.target_group_arn]
    content {
      target_group_arn = load_balancer.value
      container_name   = "app"
      container_port   = var.port
    }
  }

  lifecycle {
    # CI deploys new task definition revisions (one per commit); Terraform must not roll them back.
    ignore_changes = [task_definition]
  }
}
