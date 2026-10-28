"""
Reports (portal spec 2.5) and the communication log (2.8). Each report is computed here once and
used by both the screen (JSON) and its CSV export, so the two always agree. Dates are IST days.
"""

import datetime as dt

from django.db.models import Count, OuterRef, Prefetch, Q, Subquery, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from apps.jobs.models import FollowUpItem
from apps.schedules.models import Attempt, Outcome, Response, Schedule
from apps.subscribers.models import Subscriber

MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 7
A = Attempt.DeliveryStatus
DELIVERED = (A.DELIVERED, A.READ)
# Follow-up ageing buckets: (key, lower bound in hours, upper bound in hours or None).
AGEING_BUCKETS = (
    ("under_4h", 0, 4),
    ("4_to_24h", 4, 24),
    ("1_to_3_days", 24, 72),
    ("over_3_days", 72, None),
)
FAILED_LOOKBACK_DAYS = 30


def date_range(params) -> tuple[dt.date, dt.date]:
    """`date_from` / `date_to` (YYYY-MM-DD, inclusive); the last 7 days by default."""
    today = timezone.localdate()
    errors = {}
    values = {}
    for key, default in (
        ("date_from", today - dt.timedelta(days=DEFAULT_RANGE_DAYS - 1)),
        ("date_to", today),
    ):
        raw = (params.get(key) or "").strip()
        try:
            parsed = parse_date(raw) if raw else default
        except ValueError:  # well-formed but impossible, e.g. 2026-13-01
            parsed = None
        if parsed is None:
            errors[key] = ["Use YYYY-MM-DD."]
        values[key] = parsed
    if not errors and values["date_from"] > values["date_to"]:
        errors["date_to"] = ["Must be on or after the start date."]
    if not errors and (values["date_to"] - values["date_from"]).days >= MAX_RANGE_DAYS:
        errors["date_from"] = [f"Pick at most {MAX_RANGE_DAYS} days."]
    if errors:
        raise ValidationError(errors)
    return values["date_from"], values["date_to"]


def _area_filter(queryset, area, path):
    if not area:
        return queryset
    if area == "none":
        return queryset.filter(**{f"{path}__isnull": True})
    return queryset.filter(**{f"{path}_id": area})


# --- Reminder results ---------------------------------------------------------------------------


def reminder_results(tenant, date_from, date_to, area=None, cylinder_type=None) -> dict:
    """
    Messages sent in the range and what came of them. `reminded` is the number of bookings with at
    least one reminder in the range; outcome shares are of those bookings (`awaiting` = no outcome).
    """
    attempts = Attempt.objects.filter(tenant=tenant, sent_at__date__range=(date_from, date_to))
    attempts = _area_filter(attempts, area, "schedule__subscriber__area")
    if cylinder_type:
        attempts = attempts.filter(schedule__custom_fields__cylinder_type=cylinder_type)
    messages = attempts.filter(channel=Attempt.Channel.WHATSAPP)
    calls = attempts.filter(channel=Attempt.Channel.VOICE)
    reminded = Schedule.objects.filter(tenant=tenant, pk__in=attempts.values("schedule_id"))
    total = reminded.count()
    # order_by() clears Meta.ordering, which would otherwise split the GROUP BY.
    grouped = reminded.values("outcome").annotate(n=Count("id")).order_by()
    by_outcome = dict(grouped.values_list("outcome", "n"))
    outcomes = []
    for key in [*Outcome.values, ""]:
        count = by_outcome.get(key, 0)
        if key == Outcome.SKIPPED and not count:
            continue
        outcomes.append(
            {
                "outcome": key or "awaiting",
                "count": count,
                "share": round(count * 100 / total, 1) if total else 0.0,
            }
        )
    return {
        "date_from": date_from,
        "date_to": date_to,
        "sent": messages.count(),
        "delivered": messages.filter(delivery_status__in=DELIVERED).count(),
        "failed": messages.filter(delivery_status=A.FAILED).count(),
        "replied": messages.filter(responses__isnull=False).distinct().count(),
        "calls": calls.count(),
        "calls_answered": calls.filter(responses__isnull=False).distinct().count(),
        "cost_paise": attempts.aggregate(total=Sum("cost_paise"))["total"] or 0,
        "reminded": total,
        "outcomes": outcomes,
    }


# --- Follow-up ageing ---------------------------------------------------------------------------


def follow_up_ageing(tenant, area=None, outcome=None, now=None) -> dict:
    """Open and contacted follow-up items by how long they have waited, per outcome."""
    now = now or timezone.now()
    items = FollowUpItem.objects.filter(tenant=tenant).exclude(status=FollowUpItem.Status.RESOLVED)
    items = _area_filter(items, area, "schedule__subscriber__area")
    if outcome:
        items = items.filter(outcome=outcome)
    aggregates = {}
    for key, low, high in AGEING_BUCKETS:
        condition = Q(opened_at__lte=now - dt.timedelta(hours=low))
        if high is not None:
            condition &= Q(opened_at__gt=now - dt.timedelta(hours=high))
        aggregates[key] = Count("id", filter=condition)
    grouped = items.values("outcome").annotate(total=Count("id"), **aggregates)
    rows = list(grouped.order_by("outcome"))
    totals = {key: sum(r[key] for r in rows) for key, _low, _high in AGEING_BUCKETS}
    return {
        "total": sum(r["total"] for r in rows),
        "buckets": [{"bucket": key, "count": totals[key]} for key, _l, _h in AGEING_BUCKETS],
        "by_outcome": rows,
        "generated_at": now,
    }


# --- Subscriber health --------------------------------------------------------------------------


def _with_last_failure(subscribers, now):
    """Annotate the latest WhatsApp attempt's status and failure reason (last 30 days)."""
    latest = Attempt.objects.filter(
        schedule__subscriber=OuterRef("pk"),
        channel=Attempt.Channel.WHATSAPP,
        sent_at__gte=now - dt.timedelta(days=FAILED_LOOKBACK_DAYS),
    ).order_by("-sent_at")
    return subscribers.annotate(
        last_status=Subquery(latest.values("delivery_status")[:1]),
        last_failure=Subquery(latest.values("failure_reason")[:1]),
    )


def subscriber_health(tenant, area=None, now=None) -> dict:
    """
    Active subscribers who can't be reminded well: no WhatsApp consent, opted out, or whose
    latest WhatsApp message in the last 30 days failed (e.g. not on WhatsApp).
    """
    now = now or timezone.now()
    subscribers = Subscriber.objects.filter(tenant=tenant, status=Subscriber.Status.ACTIVE)
    subscribers = _area_filter(subscribers, area, "area")
    annotated = _with_last_failure(subscribers, now)
    counts = annotated.aggregate(
        total=Count("id"),
        no_consent=Count("id", filter=Q(whatsapp_consent=False)),
        opted_out=Count("id", filter=Q(opted_out=True)),
        failed_numbers=Count("id", filter=Q(last_status=A.FAILED)),
    )
    by_area = []
    for row in (
        annotated.values("area__name")
        .annotate(
            total=Count("id"),
            no_consent=Count("id", filter=Q(whatsapp_consent=False)),
            opted_out=Count("id", filter=Q(opted_out=True)),
            failed_numbers=Count("id", filter=Q(last_status=A.FAILED)),
        )
        .order_by("area__name")
    ):
        by_area.append({**row, "area": row.pop("area__name")})
    return {**counts, "by_area": by_area}


def subscriber_health_rows(tenant, area=None, now=None):
    """The subscribers behind the counts, for the CSV."""
    now = now or timezone.now()
    subscribers = Subscriber.objects.filter(tenant=tenant, status=Subscriber.Status.ACTIVE)
    subscribers = _area_filter(subscribers, area, "area").select_related("area")
    annotated = _with_last_failure(subscribers, now).filter(
        Q(whatsapp_consent=False) | Q(opted_out=True) | Q(last_status=A.FAILED)
    )
    return annotated.order_by("area__name", "customer_name")


# --- Communication log / message log ------------------------------------------------------------


def communication_log(tenant, params):
    """Every attempt (message or call) with its replies, newest first."""
    queryset = (
        Attempt.objects.filter(tenant=tenant)
        .select_related("schedule__subscriber__area")
        .prefetch_related(Prefetch("responses", queryset=Response.objects.order_by("received_at")))
    )
    if params.get("date_from") or params.get("date_to"):
        date_from, date_to = date_range(params)
        queryset = queryset.filter(sent_at__date__range=(date_from, date_to))
    if (channel := params.get("channel")) in Attempt.Channel.values:
        queryset = queryset.filter(channel=channel)
    if (status := params.get("delivery_status")) in A.values:
        queryset = queryset.filter(delivery_status=status)
    if outcome := params.get("outcome"):
        queryset = queryset.filter(responses__outcome=outcome).distinct()
    if booking := params.get("booking"):
        queryset = queryset.filter(schedule_id=booking)
    if subscriber := params.get("subscriber"):
        queryset = queryset.filter(schedule__subscriber_id=subscriber)
    if params.get("has_recording") == "true":
        queryset = queryset.filter(responses__recording_key__gt="").distinct()
    if search := (params.get("search") or "").strip():
        queryset = queryset.filter(
            Q(schedule__booking_reference__icontains=search)
            | Q(schedule__subscriber__customer_name__icontains=search)
            | Q(schedule__subscriber__mobile_number__icontains=search)
            | Q(schedule__subscriber__external_ref__icontains=search)
        )
    return queryset.order_by("-sent_at", "-created_at")
