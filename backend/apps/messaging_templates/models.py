"""WhatsApp templates (spec 2.7, Section 6). Tenant-scoped with RLS; platform staff read all."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.constants import Language
from apps.core.models import TenantScopedModel


class VersionStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    SENT_TO_CLIENT = "sent_to_client", _("Sent to client")
    SIGNED_OFF = "signed_off", _("Signed off")
    SENT_BACK = "sent_back", _("Sent back")
    SUBMITTED = "submitted", _("Submitted to Meta")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    PAUSED = "paused", _("Paused")
    DISABLED = "disabled", _("Disabled")


def evidence_upload_to(instance, filename):
    return f"tenants/{instance.tenant_id}/whatsapp-signoff/{instance.pk}/{filename}"


class WhatsAppTemplate(TenantScopedModel):
    class Category(models.TextChoices):
        UTILITY = "utility", _("Utility")

    name = models.CharField(max_length=512)
    language = models.CharField(max_length=2, choices=Language.choices)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.UTILITY)
    schedule_type = models.ForeignKey(
        "industry.ScheduleType", on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["name", "language"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name", "language"], name="whatsapp_template_name_language"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.language})"


class WhatsAppTemplateVersion(TenantScopedModel):
    template = models.ForeignKey(
        WhatsAppTemplate, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20, choices=VersionStatus.choices, default=VersionStatus.DRAFT
    )
    is_live = models.BooleanField(default=False)
    header_text = models.CharField(max_length=60, blank=True)
    body = models.TextField(max_length=1024)
    footer = models.CharField(max_length=60, blank=True)
    buttons = models.JSONField(
        default=list, blank=True, help_text='[{"text": ..., "outcome": ...}]'
    )
    sample_values = models.JSONField(default=dict, blank=True)
    provider_template_id = models.CharField(max_length=100, blank=True)
    rejection_reason = models.TextField(blank=True)
    signoff_evidence = models.FileField(upload_to=evidence_upload_to, blank=True, max_length=300)
    signed_off_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    signed_off_at = models.DateTimeField(null=True, blank=True)
    signoff_method = models.CharField(
        max_length=10, blank=True, choices=[("client", "Client"), ("manual", "Manual")]
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "template", "version"], name="whatsapp_version_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant", "template"],
                condition=Q(is_live=True),
                name="whatsapp_one_live_version",
            ),
        ]

    def __str__(self):
        return f"{self.template} v{self.version} [{self.status}]"


class TemplateStatusEvent(TenantScopedModel):
    """One row per state change of a version (Workflow 3.4), shown as the version history."""

    version = models.ForeignKey(
        WhatsAppTemplateVersion, on_delete=models.CASCADE, related_name="events"
    )
    action = models.CharField(max_length=30)
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]
