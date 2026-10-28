"""
Workflows 3.1 (booking to delivery) and 3.2 (follow-up), on seed_demo data (seeded outcomes).
"""

import datetime as dt
import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.models import Tenant
from apps.core.tenancy import db_context, tenant_context
from apps.jobs.models import FollowUpItem, Job, StaffMember
from apps.schedules import services
from apps.schedules.hooks import schedule_date_changed
from apps.schedules.models import Schedule
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()
TOMORROW = TODAY + dt.timedelta(days=1)


@pytest.fixture
def demo(gas_template, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("demo-password-for-tests-1\n"))
    call_command(
        "seed_demo",
        "--allow-non-debug",
        "--password-stdin",
        "--subscribers",
        "120",
        stdout=io.StringIO(),
    )
    return Tenant.objects.get(name="Demo Gas Agency")


@pytest.fixture
def hook_calls():
    calls = []

    def receiver(sender, schedule, previous_date, **kwargs):
        calls.append((schedule.booking_reference, previous_date, schedule.scheduled_date))

    schedule_date_changed.connect(receiver, weak=False)
    yield calls
    schedule_date_changed.disconnect(receiver)


def as_role(api, tenant, role):
    user = factories.make_user(f"{role}-{factories.next_id()}@demo.test")
    factories.add_membership(user, tenant, role)
    sign_in(api, user)
    return api


def pick(tenant, **filters):
    with tenant_context(tenant):
        schedule = (
            Schedule.objects.filter(**filters)
            .exclude(status="cancelled")
            .select_related("subscriber__area__default_staff")
            .order_by("booking_reference")
            .first()
        )
    assert schedule is not None, filters
    return schedule


def run(tenant, fn, *args, **kwargs):
    with tenant_context(tenant):
        return fn(*args, **kwargs)


def reload(tenant, obj):
    with tenant_context(tenant):
        obj.refresh_from_db()
    return obj


def kinds(tenant, schedule):
    with tenant_context(tenant):
        return list(schedule.events.values_list("kind", flat=True))


# --- Workflow 3.1: booking to delivery ---------------------------------------------------------


def test_booking_without_date_waits_until_a_date_is_set(demo, hook_calls):
    schedule = pick(demo, scheduled_date=None)
    assert schedule.status == "booked" and schedule.outcome == ""
    run(demo, services.set_date, schedule, TOMORROW)
    schedule = reload(demo, schedule)
    assert schedule.scheduled_date == TOMORROW and schedule.status == "booked"
    assert hook_calls == [(schedule.booking_reference, None, TOMORROW)]
    assert kinds(demo, schedule)[-1] == "date_set"


def test_confirmed_creates_a_job_for_the_areas_default_staff(demo):
    schedule = pick(
        demo,
        scheduled_date__gt=TOMORROW,
        outcome="",
        status="booked",
        subscriber__area__isnull=False,
        subscriber__whatsapp_consent=True,
        subscriber__opted_out=False,
        subscriber__status="active",
    )
    run(demo, services.record_outcome, schedule, "confirmed")
    with tenant_context(demo):
        job = Job.objects.get(schedule=schedule)
    assert job.status == "pending" and job.scheduled_date == schedule.scheduled_date
    assert job.assigned_staff_id == schedule.subscriber.area.default_staff_id
    assert reload(demo, schedule).status == "closed"
    assert kinds(demo, schedule)[-2:] == ["outcome_set", "job_created"]


def test_confirmed_without_an_area_is_unassigned(demo):
    schedule = pick(demo, scheduled_date__gt=TOMORROW, outcome="", status="booked")
    with tenant_context(demo):
        schedule.subscriber.area = None
        schedule.subscriber.save()
    run(demo, services.record_outcome, schedule, "confirmed")
    with tenant_context(demo):
        assert Job.objects.get(schedule=schedule).assigned_staff is None


def test_seeded_confirmed_bookings_appear_on_deliveries_grouped_by_area(api, demo):
    as_role(api, demo, "office_staff")
    data = api.get("/api/portal/deliveries", {"date": TOMORROW.isoformat()}).json()
    with tenant_context(demo):
        expected = Job.objects.filter(scheduled_date=TOMORROW).exclude(status="cancelled").count()
    assert data["confirmed"] == expected > 0
    assert sum(len(a["jobs"]) for a in data["areas"]) == expected
    names = [a["area_name"] for a in data["areas"]]
    assert names == sorted(n for n in names if n) + ([None] if None in names else [])
    first = data["areas"][0]["jobs"][0]
    assert {
        "booking_reference",
        "customer_name",
        "mobile_number",
        "address",
        "cylinder_type",
        "quantity",
        "assigned_staff_name",
    } <= set(first)
    assert set(data["not_confirmed"]) <= {
        "awaiting",
        "reschedule",
        "not_available",
        "needs_review",
        "no_response",
        "failed",
        "opted_out",
        "skipped",
    }


def test_deliveries_default_to_the_next_delivery_day(api, demo):
    as_role(api, demo, "owner")
    data = api.get("/api/portal/deliveries").json()
    expected = run(demo, services.next_delivery_day, demo)
    assert data["date"] == expected.isoformat()
    assert expected.weekday() != 6  # Sunday is not a gas delivery day (Mon-Sat)


def test_next_delivery_day_skips_sunday(demo):
    saturday = dt.date(2026, 10, 31)
    assert run(demo, services.next_delivery_day, demo, saturday) == dt.date(2026, 11, 2)


def test_manager_reassigns_a_job_office_staff_cannot(api, demo):
    with tenant_context(demo):
        job = Job.objects.filter(status="pending").select_related("schedule").first()
        other = StaffMember.objects.exclude(pk=job.assigned_staff_id).filter(is_active=True).first()
    as_role(api, demo, "office_staff")
    assert (
        api.post(
            f"/api/portal/jobs/{job.pk}/reassign", {"assigned_staff": str(other.pk)}, format="json"
        ).status_code
        == 403
    )
    as_role(api, demo, "manager")
    response = api.post(
        f"/api/portal/jobs/{job.pk}/reassign", {"assigned_staff": str(other.pk)}, format="json"
    )
    assert response.status_code == 200 and response.json()["assigned_staff_name"] == other.name
    assert kinds(demo, job.schedule)[-1] == "job_reassigned"
    with db_context(platform=True):
        assert AuditLog.objects.filter(object_id=str(job.pk), action="update").exists()


def test_reassign_rules(api, demo):
    with tenant_context(demo):
        job = Job.objects.filter(status="pending").first()
        done = Job.objects.filter(status="done").first()
        inactive = StaffMember.objects.create(tenant=demo, name="Left the company", is_active=False)
    as_role(api, demo, "manager")
    url = f"/api/portal/jobs/{job.pk}/reassign"
    assert (
        api.post(url, {"assigned_staff": str(inactive.pk)}, format="json").json()["code"]
        == "staff_inactive"
    )
    foreign = factories.make_tenant("Other")
    with tenant_context(foreign):
        theirs = StaffMember.objects.create(tenant=foreign, name="Theirs")
    assert api.post(url, {"assigned_staff": str(theirs.pk)}, format="json").status_code == 400
    assert api.post(url, {"assigned_staff": None}, format="json").json()["assigned_staff"] is None
    response = api.post(
        f"/api/portal/jobs/{done.pk}/reassign", {"assigned_staff": None}, format="json"
    )
    assert response.json()["code"] == "job_not_pending"


def test_a_later_answer_replaces_the_outcome_and_history_keeps_both(demo):
    schedule = pick(demo, outcome="confirmed", scheduled_date__gte=TODAY)
    run(demo, services.record_outcome, schedule, "not_available")
    with tenant_context(demo):
        assert not Job.objects.filter(schedule=schedule).exclude(status="cancelled").exists()
        item = FollowUpItem.objects.get(schedule=schedule)
    assert item.outcome == "not_available" and item.status == "open"
    run(demo, services.record_outcome, schedule, "confirmed")
    item = reload(demo, item)
    assert item.status == "resolved" and item.resolution == "confirmed"
    with tenant_context(demo):
        assert Job.objects.filter(schedule=schedule, status="pending").count() == 1
        assert Job.objects.filter(schedule=schedule, status="cancelled").count() == 1
        outcomes = [e.data["outcome"] for e in schedule.events.filter(kind="outcome_set")]
    assert outcomes[-2:] == ["not_available", "confirmed"]


def test_cancelled_booking_takes_no_more_changes(demo):
    schedule = pick(demo, outcome="confirmed", scheduled_date__gte=TODAY)
    run(demo, services.cancel_booking, schedule, "Customer moved away")
    schedule = reload(demo, schedule)
    assert schedule.status == "cancelled" and schedule.cancel_reason == "Customer moved away"
    with tenant_context(demo):
        assert Job.objects.get(schedule=schedule).status == "cancelled"
    from apps.core.exceptions import ApiError

    for change in (
        lambda: services.set_date(schedule, TOMORROW),
        lambda: services.record_outcome(schedule, "confirmed"),
    ):
        with pytest.raises(ApiError) as exc:
            run(demo, change)
        assert exc.value.get_codes() == "booking_cancelled"


def test_opt_out_answer_marks_the_subscriber(demo):
    schedule = pick(demo, outcome="", status="booked", scheduled_date__gt=TOMORROW)
    run(demo, services.record_outcome, schedule, "opted_out")
    subscriber = reload(demo, schedule.subscriber)
    assert subscriber.opted_out and subscriber.opted_out_at
    with tenant_context(demo):
        assert FollowUpItem.objects.get(schedule=schedule).outcome == "opted_out"


# --- Workflow 3.2: follow-up --------------------------------------------------------------------


def test_seeded_queue_lists_every_non_confirmed_outcome_with_ageing(api, demo):
    as_role(api, demo, "office_staff")
    page = api.get("/api/portal/follow-ups").json()
    assert page["count"] > 0
    rows = page["results"]
    assert {r["status"] for r in rows} <= {"open", "contacted"}
    assert "confirmed" not in {r["outcome"] for r in rows}
    for row in rows:
        minutes = row["age_minutes"]
        assert row["ageing"] == (
            "normal" if minutes < 240 else "amber" if minutes < 1440 else "red"
        )
    assert [r["age_minutes"] for r in rows] == sorted(
        (r["age_minutes"] for r in rows), reverse=True
    )
    review = api.get("/api/portal/follow-ups", {"outcome": "needs_review"}).json()["results"]
    if review:
        assert review[0]["last_reply"]  # the customer's message is shown
    failed = api.get("/api/portal/follow-ups", {"outcome": "failed"}).json()["results"]
    if failed:
        assert failed[0]["failure_reason"]
    resolved = api.get("/api/portal/follow-ups", {"status": "resolved"}).json()["results"]
    assert all(r["status"] == "resolved" for r in resolved)


@pytest.mark.parametrize("age", ["normal", "amber", "red"])
def test_age_filter(api, demo, age):
    as_role(api, demo, "manager")
    rows = api.get("/api/portal/follow-ups", {"age": age}).json()["results"]
    assert all(r["ageing"] == age for r in rows)


def test_reschedule_with_a_picked_day_moves_the_booking_and_restarts_the_cycle(demo, hook_calls):
    schedule = pick(demo, outcome="", status="booked", scheduled_date__gt=TOMORROW)
    old_day, new_day = schedule.scheduled_date, schedule.scheduled_date + dt.timedelta(days=2)
    run(demo, services.record_outcome, schedule, "reschedule", new_date=new_day)
    schedule = reload(demo, schedule)
    assert (schedule.scheduled_date, schedule.status, schedule.outcome) == (new_day, "booked", "")
    assert hook_calls == [(schedule.booking_reference, old_day, new_day)]
    with tenant_context(demo):
        assert (
            not FollowUpItem.objects.filter(schedule=schedule).exclude(status="resolved").exists()
        )


def test_follow_up_set_new_date_resolves_and_restarts(api, demo, hook_calls):
    # "Change date" without a picked day goes to the queue; staff then set the day.
    booked = pick(demo, outcome="", status="booked", scheduled_date__gt=TOMORROW)
    run(demo, services.record_outcome, booked, "reschedule")
    item = pick_item(demo, outcome="reschedule", schedule=booked)
    as_role(api, demo, "office_staff")
    new_day = TODAY + dt.timedelta(days=3)
    response = api.post(
        f"/api/portal/follow-ups/{item.pk}/set-date",
        {"scheduled_date": new_day.isoformat()},
        format="json",
    )
    assert response.status_code == 200 and response.json()["status"] == "resolved"
    schedule = reload(demo, item.schedule)
    assert (schedule.scheduled_date, schedule.status, schedule.outcome) == (new_day, "booked", "")
    assert hook_calls[-1][2] == new_day
    listed = api.get("/api/portal/follow-ups", {"outcome": "reschedule"}).json()["results"]
    assert str(item.pk) not in [r["id"] for r in listed]


def pick_item(tenant, **filters):
    with tenant_context(tenant):
        item = (
            FollowUpItem.objects.filter(status="open", **filters)
            .select_related("schedule")
            .order_by("opened_at")
            .first()
        )
    assert item is not None, filters
    return item


def test_mark_contacted_stays_open_and_is_logged(api, demo):
    item = pick_item(demo, outcome="not_available")
    as_role(api, demo, "office_staff")
    response = api.post(
        f"/api/portal/follow-ups/{item.pk}/contacted",
        {"note": "Will be home Friday"},
        format="json",
    )
    data = response.json()
    assert data["status"] == "contacted" and data["contacted_at"]
    assert data["notes"][-1]["text"] == "Will be home Friday"
    listed = api.get("/api/portal/follow-ups", {"status": "contacted"}).json()["results"]
    assert str(item.pk) in [r["id"] for r in listed]
    assert kinds(demo, item.schedule)[-1] == "follow_up_contacted"


def test_add_note(api, demo):
    item = pick_item(demo)
    as_role(api, demo, "manager")
    assert (
        api.post(f"/api/portal/follow-ups/{item.pk}/note", {"text": ""}, format="json").status_code
        == 400
    )
    data = api.post(
        f"/api/portal/follow-ups/{item.pk}/note", {"text": "Asked the neighbour"}, format="json"
    ).json()
    assert data["notes"][-1]["text"] == "Asked the neighbour" and data["status"] == "open"


def test_cancel_booking_from_the_queue(api, demo):
    item = pick_item(demo, outcome="no_response")
    as_role(api, demo, "office_staff")
    assert (
        api.post(
            f"/api/portal/follow-ups/{item.pk}/cancel-booking", {"reason": "no"}, format="json"
        ).status_code
        == 400
    )
    data = api.post(
        f"/api/portal/follow-ups/{item.pk}/cancel-booking",
        {"reason": "Customer not reachable"},
        format="json",
    ).json()
    assert data["status"] == "resolved"
    schedule = reload(demo, item.schedule)
    assert schedule.status == "cancelled"
    item = reload(demo, item)
    assert item.resolution == "cancelled"


def test_resolve_and_no_actions_after(api, demo):
    item = pick_item(demo)
    as_role(api, demo, "owner")
    data = api.post(
        f"/api/portal/follow-ups/{item.pk}/resolve", {"note": "Sorted by phone"}, format="json"
    ).json()
    assert data["status"] == "resolved" and data["notes"][-1]["text"] == "Sorted by phone"
    for path, body in [
        ("contacted", {}),
        ("note", {"text": "x"}),
        ("resolve", {}),
        ("set-date", {"scheduled_date": TOMORROW.isoformat()}),
    ]:
        response = api.post(f"/api/portal/follow-ups/{item.pk}/{path}", body, format="json")
        assert response.status_code == 409 and response.json()["code"] == "follow_up_resolved", path


def test_follow_up_actions_are_audited(api, demo):
    item = pick_item(demo)
    as_role(api, demo, "manager")
    api.post(f"/api/portal/follow-ups/{item.pk}/contacted", {}, format="json")
    api.post(f"/api/portal/follow-ups/{item.pk}/resolve", {}, format="json")
    with db_context(platform=True):
        actions = list(
            AuditLog.objects.filter(object_id=str(item.pk))
            .order_by("created_at")
            .values_list("action", flat=True)
        )
    assert actions == ["status_change", "status_change"]


def test_support_session_can_view_but_not_work_the_queue(api, super_admin, demo):
    from apps.platform_admin.services import start_support_session
    from apps.platform_admin.tenancy import SUPPORT_SESSION_KEY

    sign_in(api, super_admin)
    support = start_support_session(super_admin, demo, "Checking the follow-up queue")
    session = api.session
    session[SUPPORT_SESSION_KEY] = str(support.pk)
    session.save()
    item = pick_item(demo)
    assert api.get("/api/portal/follow-ups").status_code == 200
    assert (
        api.post(f"/api/portal/follow-ups/{item.pk}/resolve", {}, format="json").status_code == 403
    )


def test_other_tenants_cannot_see_the_queue(api, demo):
    other = factories.make_tenant("Elsewhere")
    as_role(api, other, "owner")
    assert api.get("/api/portal/follow-ups").json()["count"] == 0
    item = pick_item(demo)
    assert (
        api.post(f"/api/portal/follow-ups/{item.pk}/resolve", {}, format="json").status_code == 404
    )
