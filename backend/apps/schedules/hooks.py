"""
Hooks the reminder engine (Phase 2) connects to. Bookings call them; nothing here sends messages.

    from apps.schedules.hooks import schedule_date_changed

    @receiver(schedule_date_changed)
    def plan_reminder(sender, schedule, previous_date, **kwargs): ...
"""

from django.dispatch import Signal

# Sent after a booking gets a delivery date or a new one (set, bulk set, reschedule, follow-up).
# The reminder cycle restarts from here. Receivers get `schedule` and `previous_date`.
schedule_date_changed = Signal()


def on_schedule_date_changed(schedule, previous_date=None):
    schedule_date_changed.send(
        sender=type(schedule), schedule=schedule, previous_date=previous_date
    )
