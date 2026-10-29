"""Change requests raised by clients about message content (admin spec 2.9, Workflow 3.6)."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class ChangeRequest(TenantScopedModel):
    class RequestType(models.TextChoices):
        NEW_TEMPLATE = "new_template", _("New template")
        EDIT_TEMPLATE = "edit_template", _("Edit a template")
        NEW_LANGUAGE = "new_language", _("New language")
        VOICE_SCRIPT = "voice_script", _("Voice script")
        PLAN_CHANGE = "plan_change", _("Plan change")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        IN_PROGRESS = "in_progress", _("In progress")
        DONE = "done", _("Done")
        DECLINED = "declined", _("Declined")

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    request_type = models.CharField(max_length=20, choices=RequestType.choices)
    subject = models.CharField(max_length=150)
    description = models.TextField(max_length=2000, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    linked_version = models.ForeignKey(
        "messaging_templates.WhatsAppTemplateVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="change_requests",
    )
    decline_reason = models.TextField(max_length=500, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "status"], name="change_request_status")]

    def __str__(self):
        return f"{self.subject} [{self.status}]"

    @property
    def is_closed(self):
        return self.status in (self.Status.DONE, self.Status.DECLINED)


class ChangeRequestComment(TenantScopedModel):
    """The thread between the client and platform staff."""

    change_request = models.ForeignKey(
        ChangeRequest, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    from_staff = models.BooleanField(default=False)
    body = models.TextField(max_length=2000)

    class Meta:
        ordering = ["created_at"]
