"""
Bookings and their reminder history (portal spec 2.3, Section 6). The booking APIs and rules come
with Step 14; the reminder engine that writes Attempts and Responses with Phase 2.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class Outcome(models.TextChoices):
    CONFIRMED = "confirmed", _("Confirmed")
    RESCHEDULE = "reschedule", _("Reschedule")
    NOT_AVAILABLE = "not_available", _("Not available")
    NEEDS_REVIEW = "needs_review", _("Needs review")
    NO_RESPONSE = "no_response", _("No response")
    FAILED = "failed", _("Failed")
    OPTED_OUT = "opted_out", _("Opted out")
    SKIPPED = "skipped", _("Skipped")


class Schedule(TenantScopedModel):
    """A booking: one dated event of the tenant's schedule type, e.g. a cylinder delivery."""

    class Status(models.TextChoices):
        BOOKED = "booked", _("Booked")
        REMINDED = "reminded", _("Reminded")
        AWAITING_REPLY = "awaiting_reply", _("Awaiting reply")
        CLOSED = "closed", _("Closed")
        CANCELLED = "cancelled", _("Cancelled")

    schedule_type = models.ForeignKey(
        "industry.ScheduleType", on_delete=models.PROTECT, related_name="+"
    )
    subscriber = models.ForeignKey(
        "subscribers.Subscriber", on_delete=models.PROTECT, related_name="schedules"
    )
    booking_reference = models.CharField(max_length=50)
    booking_date = models.DateField()
    scheduled_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.BOOKED)
    outcome = models.CharField(max_length=15, choices=Outcome.choices, blank=True)
    new_date_requested = models.DateField(null=True, blank=True)
    remarks = models.CharField(max_length=500, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=500, blank=True)
    # Set by the reminder engine (Phase 2): when the next reminder is due, and whether it is held
    # because the wallet can't pay for it (Workflow 3.4).
    reminder_due_at = models.DateTimeField(null=True, blank=True)
    reminder_held_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-booking_date", "booking_reference"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "booking_reference"], name="schedules_reference_per_tenant"
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "scheduled_date"], name="schedule_date_idx"),
            models.Index(fields=["tenant", "outcome"], name="schedule_outcome_idx"),
        ]

    def __str__(self):
        return self.booking_reference


class ScheduleEvent(TenantScopedModel):
    """One timeline entry: status change, reminder, reply, note (spec 2.3 timeline drawer)."""

    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=30)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    data = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["occurred_at", "created_at"]


class Attempt(TenantScopedModel):
    """One reminder sent: a WhatsApp message or a voice call."""

    class Channel(models.TextChoices):
        WHATSAPP = "whatsapp", _("WhatsApp")
        VOICE = "voice", _("Voice call")

    class DeliveryStatus(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENT = "sent", _("Sent")
        DELIVERED = "delivered", _("Delivered")
        READ = "read", _("Read")
        ANSWERED = "answered", _("Answered")
        FAILED = "failed", _("Failed")

    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name="attempts")
    channel = models.CharField(max_length=10, choices=Channel.choices)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=100, blank=True)
    delivery_status = models.CharField(
        max_length=10, choices=DeliveryStatus.choices, default=DeliveryStatus.QUEUED
    )
    failure_reason = models.CharField(max_length=255, blank=True)
    cost_paise = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sent_at", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider_message_id"],
                condition=~Q(provider_message_id=""),
                name="schedules_attempt_provider_id",
            )
        ]


class Response(TenantScopedModel):
    """A customer's answer to an attempt: button, text or call transcript, and its outcome."""

    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="responses")
    received_at = models.DateTimeField()
    raw_text = models.TextField(blank=True)
    button_payload = models.CharField(max_length=100, blank=True)
    transcript = models.TextField(blank=True)
    recording_key = models.CharField(max_length=300, blank=True)
    outcome = models.CharField(max_length=15, choices=Outcome.choices, blank=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)

    class Meta:
        ordering = ["received_at"]
