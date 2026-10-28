# --- HTTPS certificate ----------------------------------------------------------------------
# With route53_zone_id, validation is automatic. Without it, run a targeted apply first, add the
# printed validation record at your DNS provider, then apply the rest (README: First deploy).

resource "aws_acm_certificate" "admin" {
  domain_name       = var.admin_domain
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "certificate_validation" {
  for_each = local.use_dns ? {
    for option in aws_acm_certificate.admin.domain_validation_options : option.domain_name => option
  } : {}
  zone_id = var.route53_zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 300
}

resource "aws_acm_certificate_validation" "admin" {
  certificate_arn         = aws_acm_certificate.admin.arn
  validation_record_fqdns = local.use_dns ? [for record in aws_route53_record.certificate_validation : record.fqdn] : null
}

# --- Application Load Balancer --------------------------------------------------------------

resource "aws_lb" "main" {
  name                       = local.name
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection
  idle_timeout               = 60
}

resource "aws_lb_target_group" "api" {
  name                 = "${local.name}-api"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30
  health_check {
    path                = "/api/health"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_target_group" "admin" {
  name                 = "${local.name}-admin"
  port                 = 3000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30
  health_check {
    path                = "/login"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.admin.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.admin.arn
  }
}

# One host for the console and its API: /api/* goes to Django, everything else to Next.js, so the
# session cookie and CSRF work same-origin without CORS.
resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
  condition {
    path_pattern {
      values = ["/api/*"]
    }
  }
}

resource "aws_route53_record" "admin" {
  count   = local.use_dns ? 1 : 0
  zone_id = var.route53_zone_id
  name    = var.admin_domain
  type    = "A"
  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}

# --- Email (SES) ----------------------------------------------------------------------------

resource "aws_sesv2_configuration_set" "main" {
  configuration_set_name = local.name
  delivery_options {
    tls_policy = "REQUIRE"
  }
  reputation_options {
    reputation_metrics_enabled = true
  }
  suppression_options {
    suppressed_reasons = ["BOUNCE", "COMPLAINT"]
  }
}

resource "aws_sesv2_email_identity" "main" {
  email_identity         = var.email_domain
  configuration_set_name = aws_sesv2_configuration_set.main.configuration_set_name
  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT" # Easy DKIM with 2048-bit keys
  }
}

resource "aws_route53_record" "ses_dkim" {
  count   = local.use_dns ? 3 : 0
  zone_id = var.route53_zone_id
  name    = "${aws_sesv2_email_identity.main.dkim_signing_attributes[0].tokens[count.index]}._domainkey.${var.email_domain}"
  type    = "CNAME"
  records = ["${aws_sesv2_email_identity.main.dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
  ttl     = 1800
}
