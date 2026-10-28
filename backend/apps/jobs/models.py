from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TenantScopedModel


class StaffMember(TenantScopedModel):
    """A delivery / collection person (portal spec 2.7). Not a portal user."""

    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=13, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "tenant", name="jobs_staff_name_per_tenant")
        ]

    def __str__(self):
        return self.name
