data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs       = slice(data.aws_availability_zones.available.names, 0, 2)
  nat_count = var.nat_gateway_per_az ? length(local.azs) : 1
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

# Public: load balancer and NAT only. Private: ECS tasks. Data: RDS and Redis (no internet route).
resource "aws_subnet" "public" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  tags              = { Name = "${local.name}-public-${local.azs[count.index]}", Tier = "public" }
}

resource "aws_subnet" "private" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 10 + count.index)
  tags              = { Name = "${local.name}-private-${local.azs[count.index]}", Tier = "private" }
}

resource "aws_subnet" "data" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 20 + count.index)
  tags              = { Name = "${local.name}-data-${local.azs[count.index]}", Tier = "data" }
}

resource "aws_eip" "nat" {
  count  = local.nat_count
  domain = "vpc"
  tags   = { Name = "${local.name}-nat-${count.index}" }
}

resource "aws_nat_gateway" "main" {
  count         = local.nat_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = { Name = "${local.name}-nat-${count.index}" }
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = length(local.azs)
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[min(count.index, local.nat_count - 1)].id
  }
  tags = { Name = "${local.name}-private-${local.azs[count.index]}" }
}

resource "aws_route_table_association" "private" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table" "data" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-data" }
}

resource "aws_route_table_association" "data" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.data.id
}

# S3 traffic from the tasks stays inside AWS (free gateway endpoint, no NAT charges).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id
  tags              = { Name = "${local.name}-s3" }
}

# --- security groups ------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Load balancer: HTTPS (and HTTP redirect) from the internet"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "api" {
  name        = "${local.name}-api"
  description = "API tasks: port 8000 from the load balancer"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "admin" {
  name        = "${local.name}-admin"
  description = "Admin console tasks: port 3000 from the load balancer"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks"
  description = "Worker, beat and migration tasks: no inbound traffic"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "PostgreSQL from API and background tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "redis" {
  name        = "${local.name}-redis"
  description = "Redis from API and background tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "alb" {
  for_each          = { http = 80, https = 443 }
  security_group_id = aws_security_group.alb.id
  description       = upper(each.key)
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = each.value
  to_port           = each.value
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  for_each                     = { api = [aws_security_group.api.id, 8000], admin = [aws_security_group.admin.id, 3000] }
  security_group_id            = aws_security_group.alb.id
  description                  = "To ${each.key} tasks"
  referenced_security_group_id = each.value[0]
  ip_protocol                  = "tcp"
  from_port                    = each.value[1]
  to_port                      = each.value[1]
}

resource "aws_vpc_security_group_ingress_rule" "from_alb" {
  for_each                     = { api = [aws_security_group.api.id, 8000], admin = [aws_security_group.admin.id, 3000] }
  security_group_id            = each.value[0]
  description                  = "From the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = each.value[1]
  to_port                      = each.value[1]
}

# Tasks reach the internet through NAT (ECR, Secrets Manager, SES, WhatsApp provider).
resource "aws_vpc_security_group_egress_rule" "tasks_out" {
  for_each = {
    api   = aws_security_group.api.id
    admin = aws_security_group.admin.id
    tasks = aws_security_group.tasks.id
  }
  security_group_id = each.value
  description       = "All outbound"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "db" {
  for_each                     = { api = aws_security_group.api.id, tasks = aws_security_group.tasks.id }
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from ${each.key}"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_ingress_rule" "redis" {
  for_each                     = { api = aws_security_group.api.id, tasks = aws_security_group.tasks.id }
  security_group_id            = aws_security_group.redis.id
  description                  = "Redis from ${each.key}"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
}
