import uuid

from django.conf import settings
from django.db import models

from apps.core.models import Tenant


class AuditLogImmutableError(Exception):
    """Raised on any attempt to change or delete an audit entry."""


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditLogImmutableError("Audit entries cannot be changed.")

    def delete(self):
        raise AuditLogImmutableError("Audit entries cannot be deleted.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise AuditLogImmutableError("Audit entries cannot be changed.")


class Action:
    """Audit action names. Plain strings, so new actions need no migration."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    STATUS_CHANGE = "status_change"
    EXPORT = "export"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    ACCOUNT_LOCKED = "account_locked"
    LOGOUT = "logout"
    OTP_SETUP_STARTED = "otp_setup_started"
    OTP_ENABLED = "otp_enabled"
    OTP_FAILED = "otp_failed"
    OTP_VERIFIED = "otp_verified"
    INVITE_ACCEPTED = "invite_accepted"
    SUPPORT_START = "support_start"
    SUPPORT_END = "support_end"


class AuditLog(models.Model):
    """
    Insert-only record of every write. Changes are blocked here (save/delete/queryset) and in the
    database (trigger + the app role has no UPDATE/DELETE). Tenant-scoped with RLS; tenant is NULL
    for platform-level actions, which only platform staff can read.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Empty for system actions.",
    )
    actor_email = models.CharField(max_length=254, blank=True)
    support_session = models.ForeignKey(
        "platform_admin.SupportSession",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        db_index=False,
    )
    action = models.CharField(max_length=50)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"], name="audit_tenant_created"),
            models.Index(fields=["tenant", "object_type", "object_id"], name="audit_tenant_object"),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_email or 'system'} {self.action}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditLogImmutableError("Audit entries cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditLogImmutableError("Audit entries cannot be deleted.")
