from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel

SUPPORT_SESSION_LENGTH = timedelta(minutes=60)


class SupportSession(TenantScopedModel):
    """
    Audited support access by platform staff to one tenant (Workflow 3.3). Lasts 60 minutes.
    Tenant-scoped with RLS; platform staff read every tenant's sessions.
    """

    class EndReason(models.TextChoices):
        MANUAL = "manual", "Ended by staff"
        EXPIRED = "expired", "Expired"
        REPLACED = "replaced", "Replaced by a new session"

    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    reason = models.TextField()
    started_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=10, choices=EndReason.choices, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["tenant", "-started_at"], name="support_tenant_started")]

    def __str__(self):
        return f"{self.staff_user_id} -> {self.tenant_id} ({self.started_at:%Y-%m-%d %H:%M})"

    def is_active(self, now=None):
        return self.ended_at is None and self.expires_at > (now or timezone.now())

    def seconds_left(self, now=None):
        if not self.is_active(now):
            return 0
        return int((self.expires_at - (now or timezone.now())).total_seconds())
