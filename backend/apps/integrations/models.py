"""API keys and outgoing webhooks (portal spec 2.10 Integrations). Tenant-scoped with RLS."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel

EVENT_TYPES = [
    ("booking.created", _("Booking created")),
    ("booking.date_set", _("Delivery date set or changed")),
    ("booking.cancelled", _("Booking cancelled")),
    ("outcome.recorded", _("Customer answer recorded")),
]


class ApiKey(TenantScopedModel):
    """A key for the public API. Only its SHA-256 hash is stored; the key is shown once."""

    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=20, help_text="Shown in lists to tell keys apart")
    key_hash = models.CharField(max_length=64, unique=True)
    rate_limit_per_minute = models.PositiveIntegerField(default=120)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.prefix})"


class WebhookEndpoint(TenantScopedModel):
    url = models.URLField(max_length=500)
    events = models.JSONField(default=list, help_text="Event types to send")
    secret = models.CharField(max_length=100, help_text="HMAC-SHA256 signing secret")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        ordering = ["created_at"]


class WebhookDelivery(TenantScopedModel):
    """One event to one endpoint, retried with back-off for 24 hours."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SUCCEEDED = "succeeded", _("Delivered")
        FAILED = "failed", _("Failed")

    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    event_id = models.UUIDField(default=uuid.uuid4)
    event_type = models.CharField(max_length=40)
    payload = models.JSONField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "status", "next_attempt_at"], name="webhook_due")]
