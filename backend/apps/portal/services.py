"""Client portal Home (spec 2.1) and the first-login checklist (Workflow 3.6)."""

import datetime as dt

from django.db.models import Count, Min, Q
from django.utils import timezone

from apps.accounts.models import Membership
from apps.jobs.models import FollowUpItem
from apps.messaging_templates.models import VersionStatus, WhatsAppTemplateVersion
from apps.schedules.models import Attempt, Outcome, Schedule
from apps.subscribers.models import Area, Subscriber

# Workflow 3.6, in order. Items that are true facts (areas exist...) tick themselves; the others are
# recorded in tenant.settings["onboarding"] when the owner marks them done (or skips auto top-up).
CHECKLIST = [
    "agency_details",
    "areas",
    "reminder_settings",
    "import_subscribers",
    "import_bookings",
    "invite_team",
    "auto_top_up",
]
MANUAL = {"agency_details", "reminder_settings", "auto_top_up"}
SKIPPABLE = {"auto_top_up"}


def current_role(request) -> str:
    if getattr(request, "support_session_id", None):
        return "support"
    membership = Membership.objects.filter(
        user=request.user, tenant_id=request.tenant_id, status=Membership.Status.ACTIVE
    ).first()
    return membership.role if membership else ""


def checklist(tenant) -> dict:
    onboarding = (tenant.settings or {}).get("onboarding", {})
    done_flags = onboarding.get("done", {})
    skipped = set(onboarding.get("skipped", []))
    facts = {
        "areas": Area.objects.filter(tenant=tenant, is_active=True).exists(),
        "import_subscribers": Subscriber.objects.filter(tenant=tenant).exists(),
        "import_bookings": Schedule.objects.filter(tenant=tenant).exists(),
        "invite_team": Membership.objects.filter(tenant=tenant)
        .exclude(status=Membership.Status.DEACTIVATED)
        .count()
        > 1,
    }
    items = []
    for key in CHECKLIST:
        done = bool(done_flags.get(key)) if key in MANUAL else facts[key]
        items.append(
            {
                "key": key,
                "done": done,
                "skipped": key in skipped and not done,
                "can_skip": key in SKIPPABLE,
                "manual": key in MANUAL,
            }
        )
    complete = all(item["done"] or item["skipped"] for item in items)
    return {"items": items, "complete": complete}


def mark_checklist(tenant, key, action) -> dict:
    settings = dict(tenant.settings or {})
    onboarding = dict(settings.get("onboarding", {}))
    done = dict(onboarding.get("done", {}))
    skipped = set(onboarding.get("skipped", []))
    if action == "done":
        done[key] = timezone.now().isoformat()
        skipped.discard(key)
    elif action == "skip":
        skipped.add(key)
    else:  # undo a skip
        skipped.discard(key)
    onboarding.update(done=done, skipped=sorted(skipped))
    settings["onboarding"] = onboarding
    tenant.settings = settings
    tenant.save(update_fields=["settings", "updated_at"])
    return checklist(tenant)


def home(tenant, role) -> dict:
    """Numbers for Home. Tomorrow = the next calendar day, in the tenant's time zone (IST)."""
    now = timezone.now()
    today = timezone.localdate()
    tomorrow = today + dt.timedelta(days=1)

    booked = Schedule.objects.filter(tenant=tenant).exclude(status=Schedule.Status.CANCELLED)
    by_outcome = dict(
        booked.filter(scheduled_date=tomorrow).values_list("outcome").annotate(n=Count("id"))
    )
    awaiting = booked.filter(
        scheduled_date=tomorrow,
        outcome="",
        status__in=[Schedule.Status.REMINDED, Schedule.Status.AWAITING_REPLY],
    ).count()
    tomorrow_counts = {
        "total": sum(by_outcome.values()),
        "confirmed": by_outcome.get(Outcome.CONFIRMED, 0),
        "reschedule": by_outcome.get(Outcome.RESCHEDULE, 0),
        "not_available": by_outcome.get(Outcome.NOT_AVAILABLE, 0),
        "no_response": by_outcome.get(Outcome.NO_RESPONSE, 0),
        "awaiting_reply": awaiting,
        "needs_review": by_outcome.get(Outcome.NEEDS_REVIEW, 0),
        "not_reminded_yet": by_outcome.get("", 0) - awaiting,
    }

    queue = FollowUpItem.objects.filter(tenant=tenant).exclude(status=FollowUpItem.Status.RESOLVED)
    oldest = queue.aggregate(oldest=Min("opened_at"))["oldest"]

    alerts = []
    if tenant.status != tenant.Status.ACTIVE:
        alerts.append({"code": "tenant_suspended", "severity": "error"})
    versions = WhatsAppTemplateVersion.objects.filter(tenant=tenant)
    if versions.filter(status=VersionStatus.PAUSED).exists():
        alerts.append({"code": "template_paused", "severity": "error"})
    if not versions.filter(is_live=True).exists():
        alerts.append({"code": "no_live_template", "severity": "warn"})
    sent_today = Attempt.objects.filter(
        tenant=tenant, sent_at__date=today, channel=Attempt.Channel.WHATSAPP
    ).count()
    if sent_today >= tenant.daily_send_cap:
        alerts.append({"code": "send_cap_reached", "severity": "error"})
    from apps.billing.services import can_send, low_balance_level, wallet_for

    if not can_send(tenant):
        alerts.append({"code": "wallet_empty", "severity": "error"})
    elif wallet_for(tenant).balance_paise < low_balance_level(tenant):
        alerts.append({"code": "wallet_low", "severity": "warn"})

    return {
        "date": today,
        "tomorrow": tomorrow,
        "tomorrow_counts": tomorrow_counts,
        "follow_up": {
            "open": queue.count(),
            "oldest_minutes": int((now - oldest).total_seconds() // 60) if oldest else None,
            "by_ageing": {
                "amber": queue.filter(
                    Q(opened_at__lte=now - dt.timedelta(hours=4))
                    & Q(opened_at__gt=now - dt.timedelta(hours=24))
                ).count(),
                "red": queue.filter(opened_at__lte=now - dt.timedelta(hours=24)).count(),
            },
        },
        "no_date": booked.filter(scheduled_date=None).count(),
        "alerts": alerts,
        "sent_today": sent_today,
        "daily_send_cap": tenant.daily_send_cap,
        "checklist": checklist(tenant) if role == "owner" else None,
        "generated_at": now,
    }
