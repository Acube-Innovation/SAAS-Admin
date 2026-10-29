"""
Booking state changes (portal spec 2.3, 2.4, Workflows 3.1 and 3.2). Every change goes through
here: it updates the booking, keeps its Job and FollowUpItem in step, writes a timeline event and an
audit entry, and restarts the reminder cycle (hook) when the delivery date changes.

    booked --(date set)--> [reminder engine] --> outcome
        confirmed      -> Job for the date, assigned to the area's default staff
        reschedule     -> moved to the requested date (cycle restarts); no date -> follow-up
        anything else  -> FollowUpItem (open)
    A later outcome replaces the earlier one (latest wins); the timeline keeps every answer.
"""

import datetime as dt

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.exceptions import ApiError
from apps.jobs.models import FollowUpItem, Job

from .hooks import booking_cancelled, booking_created, on_schedule_date_changed, outcome_recorded
from .models import Outcome, Schedule, ScheduleEvent

FOLLOW_UP_OUTCOMES = {
    Outcome.RESCHEDULE,
    Outcome.NOT_AVAILABLE,
    Outcome.NEEDS_REVIEW,
    Outcome.NO_RESPONSE,
    Outcome.FAILED,
    Outcome.OPTED_OUT,
}


def event(schedule, kind, actor=None, **data):
    return ScheduleEvent.objects.create(
        tenant_id=schedule.tenant_id,
        schedule=schedule,
        kind=kind,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        data={k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in data.items()},
    )


def _audit(action, obj, before, actor=None):
    write_audit(action, obj, before=before, after=snapshot(obj))


def _ensure_editable(schedule):
    if schedule.status == Schedule.Status.CANCELLED:
        raise ApiError("booking_cancelled", _("This booking is cancelled."), 409)


def validate_new_date(value):
    if value < timezone.localdate():
        raise ApiError("invalid_date", _("The delivery date can't be in the past."), 400)


# --- jobs and follow-up items ---------------------------------------------------------------


def active_job(schedule):
    return schedule.jobs.exclude(status=Job.Status.CANCELLED).first()


def open_follow_up(schedule):
    return schedule.follow_ups.exclude(status=FollowUpItem.Status.RESOLVED).first()


def plan_job(schedule):
    """An unsaved Job for a confirmed booking, assigned to its area's default staff member."""
    area = schedule.subscriber.area
    staff = (
        area.default_staff if area and area.default_staff and area.default_staff.is_active else None
    )
    return Job(
        tenant_id=schedule.tenant_id,
        schedule=schedule,
        scheduled_date=schedule.scheduled_date,
        assigned_staff=staff,
    )


def _cancel_job(schedule, reason, actor=None):
    job = active_job(schedule)
    if job and job.status == Job.Status.PENDING:
        job.status, job.cancelled_reason = Job.Status.CANCELLED, reason
        job.save(update_fields=["status", "cancelled_reason", "updated_at"])
        event(schedule, "job_cancelled", actor, reason=reason)
    return job


def _resolve_follow_up(schedule, resolution, actor=None, note=None):
    item = open_follow_up(schedule)
    if item is None:
        return None
    before = snapshot(item)
    item.status = FollowUpItem.Status.RESOLVED
    item.resolution = resolution
    item.resolved_at = timezone.now()
    if note:
        item.notes = [*item.notes, _note(note, actor)]
    item.save()
    event(schedule, "follow_up_resolved", actor, resolution=resolution)
    _audit(Action.STATUS_CHANGE, item, before, actor)
    return item


def _note(text, actor):
    return {
        "at": timezone.now().isoformat(),
        "by": getattr(actor, "email", "") or "system",
        "text": text,
    }


# --- bookings -------------------------------------------------------------------------------


@transaction.atomic
def create_booking(tenant, data, actor=None) -> Schedule:
    schedule = Schedule.objects.create(tenant=tenant, **data)
    event(schedule, "created", actor, source="portal" if actor else "system")
    booking_created.send(sender=Schedule, schedule=schedule)
    write_audit(Action.CREATE, schedule, after=snapshot(schedule))
    if schedule.scheduled_date:
        on_schedule_date_changed(schedule, previous_date=None)
    return schedule


@transaction.atomic
def set_date(schedule, new_date, actor=None, reason="set_date") -> Schedule:
    """
    Give the booking a (new) delivery date: the reminder cycle restarts. Any earlier outcome is
    cleared, a pending job for the old date is cancelled and an open follow-up is resolved.
    """
    schedule = Schedule.objects.select_for_update(of=("self",)).get(pk=schedule.pk)
    _ensure_editable(schedule)
    validate_new_date(new_date)
    before = snapshot(schedule)
    previous = schedule.scheduled_date
    schedule.scheduled_date = new_date
    schedule.status = Schedule.Status.BOOKED
    schedule.outcome = ""
    schedule.new_date_requested = None
    schedule.save()
    _cancel_job(schedule, "date_changed", actor)
    _resolve_follow_up(schedule, "new_date", actor)
    event(
        schedule,
        "date_set" if previous is None else "date_changed",
        actor,
        previous_date=previous,
        scheduled_date=new_date,
        reason=reason,
    )
    _audit(Action.UPDATE, schedule, before, actor)
    on_schedule_date_changed(schedule, previous_date=previous)
    return schedule


def bulk_set_date(tenant_id, schedule_ids, new_date, actor=None) -> dict:
    """One date for many bookings. Cancelled bookings are skipped and reported."""
    validate_new_date(new_date)
    updated, skipped = [], []
    with transaction.atomic():
        for schedule in Schedule.objects.filter(tenant_id=tenant_id, pk__in=schedule_ids):
            if schedule.status == Schedule.Status.CANCELLED:
                skipped.append(str(schedule.pk))
                continue
            set_date(schedule, new_date, actor, reason="bulk_set_date")
            updated.append(str(schedule.pk))
    missing = sorted({str(i) for i in schedule_ids} - set(updated) - set(skipped))
    return {"updated": updated, "skipped_cancelled": skipped, "not_found": missing}


@transaction.atomic
def cancel_booking(schedule, reason, actor=None) -> Schedule:
    """Cancel: no further messages, the job is cancelled and any follow-up is resolved."""
    schedule = Schedule.objects.select_for_update(of=("self",)).get(pk=schedule.pk)
    _ensure_editable(schedule)
    before = snapshot(schedule)
    schedule.status = Schedule.Status.CANCELLED
    schedule.cancelled_at = timezone.now()
    schedule.cancel_reason = reason
    schedule.save()
    _cancel_job(schedule, "booking_cancelled", actor)
    _resolve_follow_up(schedule, "cancelled", actor)
    event(schedule, "cancelled", actor, reason=reason)
    booking_cancelled.send(sender=Schedule, schedule=schedule)
    _audit(Action.STATUS_CHANGE, schedule, before, actor)
    return schedule


@transaction.atomic
def record_outcome(schedule, outcome, *, new_date=None, response=None, actor=None) -> Schedule:
    """
    Apply a customer's answer (from the reminder engine, a call, or staff). Latest wins.
    """
    schedule = (
        Schedule.objects.select_for_update(of=("self",))
        .select_related("subscriber__area__default_staff")
        .get(pk=schedule.pk)
    )
    _ensure_editable(schedule)
    if outcome not in Outcome.values:
        raise ApiError("invalid_outcome", _("Unknown outcome."), 400)
    before = snapshot(schedule)
    previous = schedule.outcome
    schedule.outcome = outcome
    schedule.status = Schedule.Status.CLOSED
    schedule.save()
    event(
        schedule,
        "outcome_set",
        actor,
        outcome=outcome,
        previous_outcome=previous,
        response=str(response.pk) if response else None,
    )
    _audit(Action.UPDATE, schedule, before, actor)
    outcome_recorded.send(sender=Schedule, schedule=schedule, outcome=outcome)

    if outcome == Outcome.OPTED_OUT:
        subscriber = schedule.subscriber
        if not subscriber.opted_out:
            subscriber.opted_out, subscriber.opted_out_at = True, timezone.now()
            subscriber.save(update_fields=["opted_out", "opted_out_at", "updated_at"])

    if outcome == Outcome.CONFIRMED:
        _resolve_follow_up(schedule, "confirmed", actor)
        if schedule.scheduled_date and active_job(schedule) is None:
            job = plan_job(schedule)
            job.save()
            event(
                schedule,
                "job_created",
                actor,
                job=str(job.pk),
                staff=job.assigned_staff.name if job.assigned_staff else None,
            )
            write_audit(Action.CREATE, job, after=snapshot(job))
        return schedule

    # Not confirmed: a delivery planned on an earlier "yes" is off.
    _cancel_job(schedule, f"outcome_{outcome}", actor)
    if outcome == Outcome.RESCHEDULE and new_date:
        schedule.new_date_requested = new_date
        schedule.save(update_fields=["new_date_requested", "updated_at"])
        if new_date >= timezone.localdate():
            # Workflow 3.2: the booking moves to the requested day and the cycle restarts.
            return set_date(schedule, new_date, actor, reason="customer_reschedule")
    # "skipped" (held while sending was paused) is not a follow-up outcome (spec 2.4).
    if outcome in FOLLOW_UP_OUTCOMES:
        open_or_update_follow_up(schedule, outcome, actor)
    return schedule


def open_or_update_follow_up(schedule, outcome, actor=None) -> FollowUpItem:
    item = open_follow_up(schedule)
    if item is None:
        item = FollowUpItem.objects.create(
            tenant_id=schedule.tenant_id, schedule=schedule, outcome=outcome
        )
        event(schedule, "follow_up_opened", actor, outcome=outcome)
        write_audit(Action.CREATE, item, after=snapshot(item))
        return item
    if item.outcome != outcome:
        before = snapshot(item)
        item.outcome = outcome
        item.status = FollowUpItem.Status.OPEN
        item.save()
        event(schedule, "follow_up_updated", actor, outcome=outcome)
        _audit(Action.UPDATE, item, before, actor)
    return item


# --- follow-up actions (Workflow 3.2) -------------------------------------------------------


def _item_for_update(item):
    item = (
        FollowUpItem.objects.select_for_update(of=("self",))
        .select_related("schedule")
        .get(pk=item.pk)
    )
    if item.status == FollowUpItem.Status.RESOLVED:
        raise ApiError("follow_up_resolved", _("This item is already resolved."), 409)
    return item


@transaction.atomic
def follow_up_set_date(item, new_date, actor=None):
    item = _item_for_update(item)
    set_date(item.schedule, new_date, actor, reason="follow_up")
    item.refresh_from_db()
    return item


@transaction.atomic
def follow_up_contacted(item, actor=None, note=""):
    """Logged and stays open (the customer may still need a new date)."""
    item = _item_for_update(item)
    before = snapshot(item)
    item.status = FollowUpItem.Status.CONTACTED
    item.contacted_at = timezone.now()
    item.notes = [*item.notes, _note(note or _("Customer contacted."), actor)]
    item.save()
    event(item.schedule, "follow_up_contacted", actor, note=note)
    _audit(Action.STATUS_CHANGE, item, before, actor)
    return item


@transaction.atomic
def follow_up_note(item, text, actor=None):
    item = _item_for_update(item)
    before = snapshot(item)
    item.notes = [*item.notes, _note(text, actor)]
    item.save(update_fields=["notes", "updated_at"])
    event(item.schedule, "note", actor, text=text)
    _audit(Action.UPDATE, item, before, actor)
    return item


@transaction.atomic
def follow_up_cancel_booking(item, reason, actor=None):
    item = _item_for_update(item)
    cancel_booking(item.schedule, reason, actor)
    item.refresh_from_db()
    return item


@transaction.atomic
def follow_up_resolve(item, actor=None, note=""):
    item = _item_for_update(item)
    _resolve_follow_up(item.schedule, "resolved", actor, note=note or None)
    item.refresh_from_db()
    return item


# --- jobs -----------------------------------------------------------------------------------


@transaction.atomic
def reassign_job(job, staff, actor=None):
    job = Job.objects.select_for_update(of=("self",)).select_related("schedule").get(pk=job.pk)
    if job.status != Job.Status.PENDING:
        raise ApiError("job_not_pending", _("Only pending deliveries can be reassigned."), 409)
    if staff is not None and not staff.is_active:
        raise ApiError("staff_inactive", _("This staff member is inactive."), 400)
    before = snapshot(job)
    previous = job.assigned_staff.name if job.assigned_staff else None
    job.assigned_staff = staff
    job.save(update_fields=["assigned_staff", "updated_at"])
    event(
        job.schedule,
        "job_reassigned",
        actor,
        previous=previous,
        staff=staff.name if staff else None,
    )
    _audit(Action.UPDATE, job, before, actor)
    return job


def next_delivery_day(tenant, after=None) -> dt.date:
    """The next delivery weekday after `after` (default today), from the tenant's reminder rule."""
    from apps.industry.services import tenant_config

    weekdays = {0, 1, 2, 3, 4, 5}
    types = tenant_config(tenant)["schedule_types"]
    if types:
        weekdays = set(types[0]["reminder_rule"].get("delivery_weekdays") or weekdays)
    day = (after or timezone.localdate()) + dt.timedelta(days=1)
    for _offset in range(7):
        if day.weekday() in weekdays:
            return day
        day += dt.timedelta(days=1)
    return day
