"""Communication log (portal spec 2.8) and Reports (2.5): totals match the seeded data."""

import csv
import datetime as dt
import io

import pytest
from django.core.management import call_command
from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.models import Tenant
from apps.core.tenancy import db_context, tenant_context
from apps.jobs.models import FollowUpItem
from apps.schedules.models import Attempt, Response, Schedule
from apps.schedules.recordings import RECORDINGS_PREFIX, recording_key
from apps.subscribers.models import Subscriber
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db
LOG = "/api/portal/communication-log"
TODAY = timezone.localdate()
WEEK = {"date_from": (TODAY - dt.timedelta(days=6)).isoformat(), "date_to": TODAY.isoformat()}


@pytest.fixture
def demo(gas_template, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("demo-password-for-tests-1\n"))
    call_command(
        "seed_demo",
        "--allow-non-debug",
        "--password-stdin",
        "--subscribers",
        "200",
        stdout=io.StringIO(),
    )
    return Tenant.objects.get(name="Demo Gas Agency")


def as_role(api, tenant, role):
    user = factories.make_user(f"{role}-{factories.next_id()}@demo.test")
    factories.add_membership(user, tenant, role)
    sign_in(api, user)
    return api


def read_csv(response):
    assert response["Content-Type"].startswith("text/csv")
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


@pytest.fixture
def s3_links(settings, monkeypatch):
    """Presigned links as on AWS (signed offline; nothing is fetched)."""
    from storages.backends.s3 import S3Storage

    from apps.core import storage

    monkeypatch.setattr(
        storage,
        "default_storage",
        S3Storage(
            bucket_name="schedule-saas-local",
            access_key="key",
            secret_key="secret",
            endpoint_url="http://storage:8333",
            region_name="ap-south-1",
            addressing_style="path",
            signature_version="s3v4",
        ),
    )
    settings.AWS_STORAGE_BUCKET_NAME = "schedule-saas-local"
    settings.AWS_S3_PUBLIC_ENDPOINT_URL = "http://localhost:8333"


# --- communication log ------------------------------------------------------------------------


def test_log_lists_every_attempt_with_replies(api, demo):
    api = as_role(api, demo, "office_staff")
    data = api.get(LOG).json()
    with tenant_context(demo):
        assert data["count"] == Attempt.objects.count()
        first = Attempt.objects.order_by("-sent_at", "-created_at").first()
    row = data["results"][0]
    assert row["id"] == str(first.pk)
    assert {"booking_reference", "customer_name", "mobile_number", "cost_paise"} <= set(row)
    calls = api.get(LOG, {"channel": "voice", "has_recording": "true"}).json()
    assert calls["count"] > 0
    reply = calls["results"][0]["responses"][0]
    assert reply["transcript"].startswith("Agent:") and reply["has_recording"] is True
    assert "recording_key" not in reply  # storage keys never leave the server


def test_log_filters(api, demo):
    api = as_role(api, demo, "manager")
    with tenant_context(demo):
        schedule = Schedule.objects.filter(attempts__isnull=False).first()
        failed = Attempt.objects.filter(delivery_status="failed").count()
        attempts = schedule.attempts.count()
    by_booking = api.get(LOG, {"booking": str(schedule.pk)}).json()
    assert by_booking["count"] == attempts
    assert api.get(LOG, {"search": schedule.booking_reference}).json()["count"] >= 1
    assert api.get(LOG, {"delivery_status": "failed"}).json()["count"] == failed
    assert api.get(LOG, {"date_from": "2026-13-01"}).status_code == 400


def test_other_agencies_attempts_are_hidden(api, demo, client_tenant):
    other = as_role(api, client_tenant, "owner")
    assert other.get(LOG).json()["count"] == 0
    with tenant_context(demo):
        attempt = Attempt.objects.filter(responses__recording_key__gt="").first()
    assert other.get(f"{LOG}/{attempt.pk}").status_code == 404
    assert other.get(f"{LOG}/{attempt.pk}/recording").status_code == 404


def test_recording_link_expires_in_5_minutes_and_is_audited(api, demo, s3_links):
    api = as_role(api, demo, "manager")
    with tenant_context(demo):
        attempt = Attempt.objects.filter(responses__recording_key__gt="").first()
        reply = attempt.responses.get()
    assert reply.recording_key == recording_key(demo.pk, attempt.pk, "wav")
    assert reply.recording_key.startswith(f"{RECORDINGS_PREFIX}{demo.pk}/")

    data = api.get(f"{LOG}/{attempt.pk}/recording").json()
    assert data["expires_in"] == 300 and "X-Amz-Expires=300" in data["url"]
    assert reply.recording_key in data["url"]
    expires_at = dt.datetime.fromisoformat(data["expires_at"])
    assert abs((expires_at - timezone.now()).total_seconds() - 300) < 5
    with db_context(platform=True):
        played = AuditLog.objects.filter(action="recording_played", tenant=demo)
        assert played.count() == 1 and played.get().object_id == str(reply.pk)
    api.get(f"{LOG}/{attempt.pk}/recording")
    with db_context(platform=True):
        assert AuditLog.objects.filter(action="recording_played", tenant=demo).count() == 2


def test_office_staff_see_transcripts_but_no_recordings(api, demo):
    api = as_role(api, demo, "office_staff")
    with tenant_context(demo):
        attempt = Attempt.objects.filter(responses__recording_key__gt="").first()
    assert api.get(f"{LOG}/{attempt.pk}").json()["responses"][0]["transcript"]
    assert api.get(f"{LOG}/{attempt.pk}/recording").status_code == 403


def test_recordings_older_than_90_days_are_gone(api, demo):
    api = as_role(api, demo, "owner")
    with tenant_context(demo):
        attempt = Attempt.objects.filter(responses__recording_key__gt="").first()
        Response.objects.filter(attempt=attempt).update(
            received_at=timezone.now() - dt.timedelta(days=91)
        )
        no_recording = Attempt.objects.filter(channel="whatsapp").first()
    assert api.get(f"{LOG}/{attempt.pk}").json()["responses"][0]["recording_expired"] is True
    response = api.get(f"{LOG}/{attempt.pk}/recording")
    assert response.status_code == 410 and response.json()["code"] == "recording_expired"
    assert api.get(f"{LOG}/{no_recording.pk}/recording").json()["code"] == "no_recording"


# --- reports ----------------------------------------------------------------------------------


def test_reminder_results_match_the_seeded_data(api, demo):
    api = as_role(api, demo, "manager")
    data = api.get("/api/portal/reports/reminder-results", WEEK).json()
    start = TODAY - dt.timedelta(days=6)
    with tenant_context(demo):
        attempts = Attempt.objects.filter(sent_at__date__range=(start, TODAY))
        messages = attempts.filter(channel="whatsapp")
        reminded = Schedule.objects.filter(pk__in=attempts.values("schedule_id"))
        assert data["sent"] == messages.count() > 0
        assert (
            data["delivered"] == messages.filter(delivery_status__in=["delivered", "read"]).count()
        )
        assert data["failed"] == messages.filter(delivery_status="failed").count()
        assert data["replied"] == messages.filter(responses__isnull=False).distinct().count()
        assert data["calls"] == attempts.filter(channel="voice").count()
        assert data["reminded"] == reminded.count()
        shares = {o["outcome"]: o for o in data["outcomes"]}
        assert shares["confirmed"]["count"] == reminded.filter(outcome="confirmed").count()
        assert shares["awaiting"]["count"] == reminded.filter(outcome="").count()
        assert sum(o["count"] for o in data["outcomes"]) == data["reminded"]
        expected = round(shares["confirmed"]["count"] * 100 / data["reminded"], 1)
        assert shares["confirmed"]["share"] == expected

        area = Subscriber.objects.exclude(area=None).values_list("area", flat=True).first()
        in_area = reminded.filter(subscriber__area=area).count()
    by_area = api.get("/api/portal/reports/reminder-results", {**WEEK, "area": str(area)}).json()
    assert by_area["reminded"] == in_area
    cylinder = api.get(
        "/api/portal/reports/reminder-results", {**WEEK, "cylinder_type": "no-such-type"}
    ).json()
    assert cylinder["sent"] == 0 and cylinder["reminded"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "yesterday"},
        {"date_from": "2026-13-01"},
        {"date_from": "2026-10-10", "date_to": "2026-10-01"},
        {"date_from": "2024-01-01", "date_to": "2026-01-01"},
    ],
)
def test_reminder_results_date_rules(api, demo, params):
    api = as_role(api, demo, "manager")
    assert api.get("/api/portal/reports/reminder-results", params).status_code == 400


def test_follow_up_ageing_matches_the_queue(api, demo):
    api = as_role(api, demo, "owner")
    data = api.get("/api/portal/reports/follow-up-ageing").json()
    now = timezone.now()
    with tenant_context(demo):
        queue = FollowUpItem.objects.exclude(status="resolved")
        assert data["total"] == queue.count() > 0
        buckets = {b["bucket"]: b["count"] for b in data["buckets"]}
        assert (
            buckets["under_4h"] == queue.filter(opened_at__gt=now - dt.timedelta(hours=4)).count()
        )
        assert (
            buckets["over_3_days"]
            == queue.filter(opened_at__lte=now - dt.timedelta(hours=72)).count()
        )
        assert sum(buckets.values()) == data["total"]
        for row in data["by_outcome"]:
            assert row["total"] == queue.filter(outcome=row["outcome"]).count()
    only = api.get("/api/portal/reports/follow-up-ageing", {"outcome": "no_response"}).json()
    assert {r["outcome"] for r in only["by_outcome"]} <= {"no_response"}


def test_subscriber_health_matches_the_seeded_data(api, demo):
    api = as_role(api, demo, "manager")
    data = api.get("/api/portal/reports/subscriber-health").json()
    with tenant_context(demo):
        active = Subscriber.objects.filter(status="active")
        assert data["total"] == active.count()
        assert data["no_consent"] == active.filter(whatsapp_consent=False).count() > 0
        assert data["opted_out"] == active.filter(opted_out=True).count()
        failed = sum(
            1
            for s in active
            if (
                last := Attempt.objects.filter(schedule__subscriber=s, channel="whatsapp")
                .filter(sent_at__gte=timezone.now() - dt.timedelta(days=30))
                .order_by("-sent_at")
                .first()
            )
            and last.delivery_status == "failed"
        )
    assert data["failed_numbers"] == failed
    assert sum(a["total"] for a in data["by_area"]) == data["total"]


@pytest.mark.parametrize(
    "url",
    [
        "/api/portal/reports/reminder-results",
        "/api/portal/reports/follow-up-ageing",
        "/api/portal/reports/subscriber-health",
        "/api/portal/reports/message-log/export",
    ],
)
def test_office_staff_have_no_reports(api, demo, url):
    assert as_role(api, demo, "office_staff").get(url).status_code == 403


# --- CSV exports ------------------------------------------------------------------------------


def test_exports_match_the_screens_and_are_audited(api, demo):
    api = as_role(api, demo, "manager")
    base = "/api/portal/reports"

    results = api.get(f"{base}/reminder-results", WEEK).json()
    rows = read_csv(api.get(f"{base}/reminder-results/export", WEEK))
    table = {r[0]: r for r in rows[1:]}
    assert rows[0] == ["metric", "count", "share_percent"]
    assert int(table["sent"][1]) == results["sent"]
    assert int(table["outcome:confirmed"][1]) == next(
        o["count"] for o in results["outcomes"] if o["outcome"] == "confirmed"
    )

    ageing = api.get(f"{base}/follow-up-ageing").json()
    rows = read_csv(api.get(f"{base}/follow-up-ageing/export"))
    assert rows[-1][0] == "total" and int(rows[-1][-1]) == ageing["total"]

    health = api.get(f"{base}/subscriber-health").json()
    rows = read_csv(api.get(f"{base}/subscriber-health/export"))
    with tenant_context(demo):
        flagged = Subscriber.objects.filter(status="active").filter(
            Q(whatsapp_consent=False) | Q(opted_out=True)
        )
        assert len(rows) - 1 >= flagged.count()
    assert sum(r[4] == "yes" for r in rows[1:]) == health["no_consent"]
    assert sum(r[6] == "yes" for r in rows[1:]) == health["failed_numbers"]

    rows = read_csv(api.get(f"{base}/message-log/export", WEEK))
    with tenant_context(demo):
        attempts = Attempt.objects.filter(
            sent_at__date__range=(TODAY - dt.timedelta(days=6), TODAY)
        )
        expected = sum(max(a.responses.count(), 1) for a in attempts)
    assert len(rows) - 1 == expected
    assert all(not r[5].startswith("+") for r in rows[1:])  # formula-safe cells ('+91...)

    with db_context(platform=True):
        exports = AuditLog.objects.filter(action="export", tenant=demo)
        assert set(exports.values_list("object_type", flat=True)) == {
            "reports.reminder-results",
            "reports.follow-up-ageing",
            "reports.subscriber-health",
            "reports.message-log",
        }
    assert api.get(f"{base}/unknown/export").status_code == 404


def test_support_can_view_reports_in_a_support_session(api, demo, super_admin):
    from apps.platform_admin.services import start_support_session
    from apps.platform_admin.tenancy import SUPPORT_SESSION_KEY

    sign_in(api, super_admin)
    support = start_support_session(super_admin, demo, "Checking reminder results")
    session = api.session
    session[SUPPORT_SESSION_KEY] = str(support.pk)
    session.save()
    assert api.get("/api/portal/reports/reminder-results").status_code == 200
    assert api.get(LOG).status_code == 200
