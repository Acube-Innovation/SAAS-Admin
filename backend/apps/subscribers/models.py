from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.core.constants import Language
from apps.core.models import TenantScopedModel


class Area(TenantScopedModel):
    """An area or route (portal spec 2.7). Jobs in it go to its default staff member."""

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, blank=True)
    default_staff = models.ForeignKey(
        "jobs.StaffMember", on_delete=models.SET_NULL, null=True, blank=True, related_name="areas"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"), "tenant", name="subscribers_area_name_per_tenant"
            )
        ]

    def __str__(self):
        return self.name


class Subscriber(TenantScopedModel):
    """A client's customer (portal spec 2.2)."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        INACTIVE = "inactive", _("Inactive")

    class ConsentSource(models.TextChoices):
        PAPER_FORM = "paper_form", _("Paper form")
        VERBAL = "verbal", _("Verbal")
        WHATSAPP_OPT_IN = "whatsapp_opt_in", _("WhatsApp opt-in")
        IMPORT = "import", _("Import")
        OTHER = "other", _("Other")

    customer_name = models.CharField(max_length=100)
    mobile_number = models.CharField(max_length=13)
    alternate_number = models.CharField(max_length=13, blank=True)
    external_ref = models.CharField(max_length=50)
    preferred_language = models.CharField(max_length=2, choices=Language.choices)
    address = models.CharField(max_length=500, blank=True)
    area = models.ForeignKey(
        Area, on_delete=models.PROTECT, null=True, blank=True, related_name="subscribers"
    )
    whatsapp_consent = models.BooleanField(default=False)
    consent_date = models.DateField(null=True, blank=True)
    consent_source = models.CharField(max_length=20, choices=ConsentSource.choices, blank=True)
    # Set only by the customer's STOP reply; the agency can't clear it (a new opt-in does).
    opted_out = models.BooleanField(default=False)
    opted_out_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    custom_fields = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["customer_name", "external_ref"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "external_ref"], name="subscribers_external_ref_per_tenant"
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "mobile_number"], name="subscriber_mobile_idx"),
            models.Index(fields=["tenant", "area"], name="subscriber_area_idx"),
        ]

    def __str__(self):
        return f"{self.customer_name} ({self.external_ref})"

    def can_be_messaged(self) -> bool:
        """Reminders go only to active subscribers with consent who haven't opted out."""
        return self.status == self.Status.ACTIVE and self.whatsapp_consent and not self.opted_out


def import_upload_path(instance, filename):
    return f"imports/{instance.tenant_id}/{instance.pk}/source.csv"


def import_error_path(instance, filename):
    return f"imports/{instance.tenant_id}/{instance.pk}/errors.csv"


class ImportJob(TenantScopedModel):
    """One CSV import (Workflow 3.5): uploaded, previewed in the background, then confirmed."""

    class Status(models.TextChoices):
        UPLOADED = "uploaded", _("Uploaded")
        PREVIEWING = "previewing", _("Checking rows")
        PREVIEWED = "previewed", _("Ready to confirm")
        CONFIRMING = "confirming", _("Importing")
        DONE = "done", _("Done")
        FAILED = "failed", _("Failed")

    entity = models.CharField(max_length=20)
    file = models.FileField(upload_to=import_upload_path, max_length=300)
    original_filename = models.CharField(max_length=255)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UPLOADED)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    created_rows = models.PositiveIntegerField(default=0)
    updated_rows = models.PositiveIntegerField(default=0)
    # The first rows' errors for the preview screen; the error file has all of them.
    errors = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error_file = models.FileField(upload_to=import_error_path, max_length=300, blank=True)
    failure_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+"
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    previewed_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.entity} import {self.original_filename} ({self.status})"
