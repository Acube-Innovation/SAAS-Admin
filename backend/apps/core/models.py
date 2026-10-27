import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from .constants import STATE_CHOICES, Language
from .tenancy import get_current_tenant_id


class BaseModel(models.Model):
    """UUID primary key and timestamps for every table."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Tenant(BaseModel):
    """A client company. Not tenant-scoped itself: it is the registry of tenants."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")

    name = models.CharField(max_length=100)
    legal_name = models.CharField(max_length=200)
    gstin = models.CharField(max_length=15, unique=True, null=True, blank=True)
    address_line1 = models.CharField(max_length=200)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, choices=STATE_CHOICES)
    pin_code = models.CharField(max_length=6)
    contact_name = models.CharField(max_length=100)
    contact_phone = models.CharField(max_length=13)
    contact_email = models.EmailField()
    # industry_template / industry_template_version are added with apps/industry in Step 6.
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True)
    default_language = models.CharField(
        max_length=2, choices=Language.choices, default=Language.MALAYALAM
    )
    timezone = models.CharField(max_length=50, default="Asia/Kolkata")
    daily_send_cap = models.PositiveIntegerField(default=5000)
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def can_send(self) -> bool:
        """Whether reminders may be sent now. Phase 2 adds wallet and send-cap checks."""
        return self.status == self.Status.ACTIVE


class TenantScopedModel(BaseModel):
    """
    Base for every tenant table. Concrete subclasses must enable Row-Level Security in their
    migration with apps.core.rls.EnableRLS; tests/test_tenancy.py fails for any that don't.
    """

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.tenant_id is None:
            self.tenant_id = get_current_tenant_id()
        super().save(*args, **kwargs)
