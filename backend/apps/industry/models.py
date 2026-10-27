"""
Industry templates (spec 2.6, Section 6). Platform-owned configuration, not tenant data: an industry
is configuration, not code. The current definition lives in ScheduleType / FieldDefinition rows;
every save also stores an immutable IndustryTemplateVersion snapshot (Workflow 3.5).
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class IndustryTemplate(BaseModel):
    key = models.SlugField(max_length=50, unique=True, help_text="Immutable, e.g. gas_agency.")
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    current_version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class IndustryTemplateVersion(BaseModel):
    template = models.ForeignKey(
        IndustryTemplate, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    definition = models.JSONField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["template", "version"], name="industry_version_unique")
        ]


class ScheduleType(BaseModel):
    """A kind of dated event, e.g. "Cylinder delivery", with its default rule and question flow."""

    template = models.ForeignKey(
        IndustryTemplate, on_delete=models.CASCADE, related_name="schedule_types"
    )
    key = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)
    default_reminder_rule = models.JSONField(default=dict)
    default_question_flow = models.JSONField(default=dict)

    class Meta:
        ordering = ["order", "key"]
        constraints = [
            models.UniqueConstraint(fields=["template", "key"], name="industry_schedule_type_key")
        ]

    def __str__(self):
        return self.name


class FieldDefinition(BaseModel):
    """
    A subscriber field (template-wide) or a schedule field (per schedule type). Core fields map to
    model columns, so only their label can change; custom fields are stored in custom_fields JSONB
    and validated against these definitions.
    """

    class Entity(models.TextChoices):
        SUBSCRIBER = "subscriber", _("Subscriber")
        SCHEDULE = "schedule", _("Schedule")

    class FieldType(models.TextChoices):
        TEXT = "text", _("Text")
        NUMBER = "number", _("Number")
        DATE = "date", _("Date")
        PHONE = "phone", _("Phone")
        CHOICE = "choice", _("Choice")
        BOOLEAN = "boolean", _("Yes / No")

    template = models.ForeignKey(IndustryTemplate, on_delete=models.CASCADE, related_name="fields")
    schedule_type = models.ForeignKey(
        ScheduleType, on_delete=models.CASCADE, null=True, blank=True, related_name="fields"
    )
    entity = models.CharField(max_length=20, choices=Entity.choices)
    key = models.CharField(max_length=50)
    label = models.CharField(max_length=100)
    field_type = models.CharField(max_length=20, choices=FieldType.choices)
    required = models.BooleanField(default=False)
    is_core = models.BooleanField(default=False)
    options = models.JSONField(default=list, blank=True)
    max_length = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["entity", "order", "key"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "entity", "schedule_type", "key"],
                name="industry_field_key",
                nulls_distinct=False,
            )
        ]

    def __str__(self):
        return f"{self.entity}.{self.key}"
