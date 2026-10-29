"""Booking events -> webhooks. Connected in IntegrationsConfig.ready()."""

from apps.schedules import hooks

from .webhooks import emit


def booking_data(schedule) -> dict:
    subscriber = schedule.subscriber
    return {
        "booking_reference": schedule.booking_reference,
        "subscriber_ref": subscriber.external_ref,
        "customer_name": subscriber.customer_name,
        "scheduled_date": schedule.scheduled_date,
        "status": schedule.status,
        "outcome": schedule.outcome or None,
        "new_date_requested": schedule.new_date_requested,
        "cancel_reason": schedule.cancel_reason or None,
    }


def _on_created(sender, schedule, **kwargs):
    emit(schedule.tenant_id, "booking.created", booking_data(schedule))


def _on_date(sender, schedule, previous_date=None, **kwargs):
    emit(
        schedule.tenant_id,
        "booking.date_set",
        {**booking_data(schedule), "previous_date": previous_date},
    )


def _on_cancelled(sender, schedule, **kwargs):
    emit(schedule.tenant_id, "booking.cancelled", booking_data(schedule))


def _on_outcome(sender, schedule, outcome, **kwargs):
    emit(schedule.tenant_id, "outcome.recorded", booking_data(schedule))


def connect():
    hooks.booking_created.connect(_on_created, dispatch_uid="webhooks_created")
    hooks.schedule_date_changed.connect(_on_date, dispatch_uid="webhooks_date")
    hooks.booking_cancelled.connect(_on_cancelled, dispatch_uid="webhooks_cancelled")
    hooks.outcome_recorded.connect(_on_outcome, dispatch_uid="webhooks_outcome")
