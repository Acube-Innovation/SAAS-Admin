from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

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


class Job(TenantScopedModel):
    """
    A delivery to make: created when a booking's outcome becomes Confirmed (Workflow 3.1),
    auto-assigned to the area's default staff member, reassignable.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        DONE = "done", _("Done")
        CANCELLED = "cancelled", _("Cancelled")

    schedule = models.ForeignKey(
        "schedules.Schedule", on_delete=models.CASCADE, related_name="jobs"
    )
    scheduled_date = models.DateField()
    assigned_staff = models.ForeignKey(
        StaffMember, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    cancelled_reason = models.CharField(max_length=100, blank=True)
    # Set when the delivery list is printed; a later cancellation is flagged to the office.
    printed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["scheduled_date", "created_at"]
        constraints = [
            # At most one live job per booking.
            models.UniqueConstraint(
                fields=["schedule"],
                condition=~Q(status="cancelled"),
                name="jobs_one_active_job_per_schedule",
            )
        ]
        indexes = [models.Index(fields=["tenant", "scheduled_date"], name="job_date_idx")]


class FollowUpItem(TenantScopedModel):
    """A booking whose outcome needs a person (Workflow 3.2): every outcome except Confirmed."""

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        CONTACTED = "contacted", _("Contacted")
        RESOLVED = "resolved", _("Resolved")

    schedule = models.ForeignKey(
        "schedules.Schedule", on_delete=models.CASCADE, related_name="follow_ups"
    )
    outcome = models.CharField(max_length=15)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    # [{"at": iso, "by": email, "text": "..."}], newest last
    notes = models.JSONField(default=list, blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.CharField(max_length=30, blank=True)
    opened_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["opened_at"]
        constraints = [
            # At most one unresolved item per booking; a newer outcome updates it.
            models.UniqueConstraint(
                fields=["schedule"],
                condition=~Q(status="resolved"),
                name="jobs_one_open_follow_up_per_schedule",
            )
        ]
        indexes = [models.Index(fields=["tenant", "status"], name="follow_up_status_idx")]
