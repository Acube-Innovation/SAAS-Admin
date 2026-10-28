"""Bookings API (portal spec 2.3): field rules, list filters, bulk date, cancel, timeline, roles."""

import csv
import datetime as dt
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.tenancy import db_context, tenant_context
from apps.jobs.models import Job, StaffMember
from apps.schedules import services
from apps.schedules.hooks import schedule_date_changed
from apps.schedules.models import Attempt, Response, Schedule, ScheduleEvent
from apps.subscribers.models import Area, Subscriber
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db

URL = "/api/portal/bookings"
TODAY = timezone.localdate()
TOMORROW = TODAY + dt.timedelta(days=1)
YESTERDAY = TODAY - dt.timedelta(days=1)


@pytest.fixture
def staff_api(portal):
    return portal("office_staff")


@pytest.fixture
def area(client_tenant):
    with tenant_context(client_tenant):
        driver = StaffMember.objects.create(tenant=client_tenant, name="Biju K")
        return Area.objects.create(tenant=client_tenant, name="Edappally", default_staff=driver)


@pytest.fixture
def subscriber(client_tenant, area):
    with tenant_context(client_tenant):
        return Subscriber.objects.create(
            tenant=client_tenant,
            customer_name="Asha Menon",
            mobile_number="+919847012345",
            external_ref="KL0700001",
            preferred_language="ml",
            area=area,
            address="MG Road",
            whatsapp_consent=True,
            consent_date=TODAY,
            consent_source="verbal",
        )


@pytest.fixture
def hook_calls():
    calls = []

    def receiver(sender, schedule, previous_date, **kwargs):
        calls.append((previous_date, schedule.scheduled_date))

    schedule_date_changed.connect(receiver, weak=False)
    yield calls
    schedule_date_changed.disconnect(receiver)


def payload(sub, **overrides):
    data = {
        "booking_reference": "BK1001",
        "subscriber": str(sub.pk),
        "booking_date": TODAY.isoformat(),
        "custom_fields": {"cylinder_type": "14.2 kg domestic"},
    }
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not ...}


def make_booking(tenant, subscriber, **fields):
    defaults = {
        "booking_reference": f"BK{factories.next_id()}",
        "booking_date": TODAY,
        "custom_fields": {"cylinder_type": "14.2 kg domestic", "quantity": 1},
    }
    defaults.update(fields)
    with tenant_context(tenant):
        from apps.industry.models import ScheduleType

        st = ScheduleType.objects.get(template=tenant.industry_template, key="cylinder_delivery")
        return Schedule.objects.create(
            tenant=tenant, subscriber=subscriber, schedule_type=st, **defaults
        )


def errors(response):
    assert response.status_code == 400, response.json()
    return response.json()["fields"]


# --- create ----------------------------------------------------------------------------------


def test_create_without_a_date_waits(staff_api, subscriber, hook_calls):
    response = staff_api.post(URL, payload(subscriber), format="json")
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["status"] == "booked" and data["scheduled_date"] is None
    assert data["schedule_type"] == "cylinder_delivery"
    assert data["custom_fields"] == {"cylinder_type": "14.2 kg domestic", "quantity": 1}
    assert data["subscriber_name"] == "Asha Menon" and data["area_name"] == "Edappally"
    assert data["job"] is None and data["follow_up"] is None
    assert hook_calls == []


def test_create_with_a_date_starts_the_cycle(staff_api, subscriber, hook_calls):
    response = staff_api.post(
        URL, payload(subscriber, scheduled_date=TOMORROW.isoformat()), format="json"
    )
    assert response.status_code == 201
    assert hook_calls == [(None, TOMORROW)]


def test_subscriber_by_reference(staff_api, subscriber):
    body = payload(subscriber, subscriber=..., subscriber_ref="KL0700001")
    assert staff_api.post(URL, body, format="json").json()["subscriber_name"] == "Asha Menon"
    body = payload(subscriber, booking_reference="BK2", subscriber=..., subscriber_ref="NOPE")
    assert "subscriber_ref" in errors(staff_api.post(URL, body, format="json"))


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"booking_reference": ""}, "booking_reference"),
        ({"booking_reference": "x" * 51}, "booking_reference"),
        ({"subscriber": ...}, "subscriber"),
        ({"booking_date": ...}, "booking_date"),
        ({"booking_date": TOMORROW.isoformat()}, "booking_date"),
        ({"scheduled_date": YESTERDAY.isoformat()}, "scheduled_date"),
        ({"remarks": "x" * 501}, "remarks"),
        ({"custom_fields": {}}, "custom_fields"),
        ({"custom_fields": {"cylinder_type": "Oxygen"}}, "custom_fields"),
        ({"custom_fields": {"cylinder_type": "5 kg", "quantity": 0}}, "custom_fields"),
        ({"custom_fields": {"cylinder_type": "5 kg", "quantity": 100}}, "custom_fields"),
        ({"custom_fields": {"cylinder_type": "5 kg", "quantity": 1.5}}, "custom_fields"),
        ({"schedule_type": "meter_reading"}, "schedule_type"),
    ],
)
def test_create_rules(staff_api, subscriber, overrides, field):
    assert field in errors(staff_api.post(URL, payload(subscriber, **overrides), format="json"))


def test_booking_reference_unique_within_the_tenant_only(staff_api, client_tenant, subscriber):
    make_booking(client_tenant, subscriber, booking_reference="BK1001")
    assert "booking_reference" in errors(staff_api.post(URL, payload(subscriber), format="json"))
    other = factories.make_tenant("Elsewhere")
    with tenant_context(other):
        their = Subscriber.objects.create(
            tenant=other,
            customer_name="T",
            mobile_number="+919847000009",
            external_ref="T1",
            preferred_language="ml",
        )
    with tenant_context(other):
        assert not Schedule.objects.exists()
    assert "subscriber" in errors(
        staff_api.post(URL, payload(their, booking_reference="BK9"), format="json")
    )


# --- edit, dates, cancel ------------------------------------------------------------------------


def test_patch_date_goes_through_set_date(staff_api, client_tenant, subscriber, hook_calls):
    booking = make_booking(client_tenant, subscriber)
    response = staff_api.patch(
        f"{URL}/{booking.pk}",
        {"scheduled_date": TOMORROW.isoformat(), "remarks": "Call before coming"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["remarks"] == "Call before coming"
    assert hook_calls == [(None, TOMORROW)]
    with tenant_context(client_tenant):
        kinds = list(booking.events.values_list("kind", flat=True))
    assert kinds == ["updated", "date_set"]
    removed = staff_api.patch(f"{URL}/{booking.pk}", {"scheduled_date": None}, format="json")
    assert removed.status_code == 400


def test_subscriber_and_type_cannot_change_on_edit(staff_api, client_tenant, subscriber):
    booking = make_booking(client_tenant, subscriber)
    with tenant_context(client_tenant):
        other = Subscriber.objects.create(
            tenant=client_tenant,
            customer_name="B",
            mobile_number="+919847000002",
            external_ref="KL2",
            preferred_language="ml",
        )
    staff_api.patch(f"{URL}/{booking.pk}", {"subscriber": str(other.pk)}, format="json")
    assert staff_api.get(f"{URL}/{booking.pk}").json()["subscriber"] == str(subscriber.pk)


def test_set_date_clears_the_outcome_and_restarts(staff_api, client_tenant, subscriber, hook_calls):
    booking = make_booking(client_tenant, subscriber, scheduled_date=TOMORROW)
    with tenant_context(client_tenant):
        services.record_outcome(booking, "confirmed")
    later = TODAY + dt.timedelta(days=3)
    data = staff_api.post(
        f"{URL}/{booking.pk}/set-date", {"scheduled_date": later.isoformat()}, format="json"
    ).json()
    assert (data["scheduled_date"], data["status"], data["outcome"]) == (
        later.isoformat(),
        "booked",
        "",
    )
    assert data["job"] is None  # the job for the old date is cancelled
    assert hook_calls[-1] == (TOMORROW, later)
    past = staff_api.post(
        f"{URL}/{booking.pk}/set-date", {"scheduled_date": YESTERDAY.isoformat()}, format="json"
    )
    assert past.status_code == 400 and past.json()["code"] == "invalid_date"


def test_bulk_set_date(staff_api, client_tenant, subscriber, hook_calls):
    a = make_booking(client_tenant, subscriber)
    b = make_booking(client_tenant, subscriber)
    gone = make_booking(client_tenant, subscriber, status="cancelled")
    foreign_id = "00000000-0000-0000-0000-000000000001"
    response = staff_api.post(
        f"{URL}/bulk-set-date",
        {
            "ids": [str(a.pk), str(b.pk), str(gone.pk), foreign_id],
            "scheduled_date": TOMORROW.isoformat(),
        },
        format="json",
    )
    data = response.json()
    assert sorted(data["updated"]) == sorted([str(a.pk), str(b.pk)])
    assert data["skipped_cancelled"] == [str(gone.pk)] and data["not_found"] == [foreign_id]
    assert len(hook_calls) == 2
    rows = staff_api.get(URL, {"date": TOMORROW.isoformat()}).json()["results"]
    assert {r["id"] for r in rows} == {str(a.pk), str(b.pk)}


def test_cancel_needs_a_reason_and_happens_once(staff_api, client_tenant, subscriber):
    booking = make_booking(client_tenant, subscriber)
    assert (
        staff_api.post(f"{URL}/{booking.pk}/cancel", {"reason": "no"}, format="json").status_code
        == 400
    )
    data = staff_api.post(
        f"{URL}/{booking.pk}/cancel", {"reason": "Customer cancelled"}, format="json"
    ).json()
    assert data["status"] == "cancelled" and data["cancel_reason"] == "Customer cancelled"
    again = staff_api.post(
        f"{URL}/{booking.pk}/cancel", {"reason": "Customer cancelled"}, format="json"
    )
    assert again.status_code == 409 and again.json()["code"] == "booking_cancelled"
    with db_context(platform=True):
        assert AuditLog.objects.filter(object_id=str(booking.pk), action="status_change").exists()


# --- list and timeline ---------------------------------------------------------------------------


@pytest.fixture
def listed(client_tenant, subscriber):
    with tenant_context(client_tenant):
        loose = Subscriber.objects.create(
            tenant=client_tenant,
            customer_name="Jose Thomas",
            mobile_number="+919847333333",
            external_ref="KL3",
            preferred_language="en",
        )
    return {
        "nodate": make_booking(client_tenant, subscriber, booking_reference="BK-NODATE"),
        "tomorrow": make_booking(
            client_tenant,
            subscriber,
            booking_reference="BK-TMR",
            scheduled_date=TOMORROW,
            outcome="confirmed",
            status="closed",
        ),
        "loose": make_booking(
            client_tenant, loose, booking_reference="BK-LOOSE", scheduled_date=TOMORROW
        ),
    }


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"no_date": "true"}, {"BK-NODATE"}),
        ({"date": TOMORROW.isoformat()}, {"BK-TMR", "BK-LOOSE"}),
        ({"outcome": "confirmed"}, {"BK-TMR"}),
        ({"outcome": "none"}, {"BK-NODATE", "BK-LOOSE"}),
        ({"area": "none"}, {"BK-LOOSE"}),
        ({"search": "jose"}, {"BK-LOOSE"}),
        ({"search": "98473 33333"}, {"BK-LOOSE"}),
        ({"search": "nodate"}, {"BK-NODATE"}),
    ],
)
def test_list_filters(staff_api, listed, params, expected):
    rows = staff_api.get(URL, params).json()["results"]
    assert {r["booking_reference"] for r in rows} == expected


def test_timeline_merges_events_attempts_and_replies_in_order(staff_api, client_tenant, subscriber):
    booking = make_booking(client_tenant, subscriber, scheduled_date=TOMORROW)
    now = timezone.now()
    with tenant_context(client_tenant):
        ScheduleEvent.objects.create(
            tenant=client_tenant,
            schedule=booking,
            kind="created",
            occurred_at=now - dt.timedelta(seconds=120),
        )
        attempt = Attempt.objects.create(
            tenant=client_tenant,
            schedule=booking,
            channel="whatsapp",
            sent_at=now - dt.timedelta(seconds=60),
            delivery_status="read",
            cost_paise=15,
        )
        Response.objects.create(
            tenant=client_tenant,
            attempt=attempt,
            received_at=now - dt.timedelta(seconds=30),
            raw_text="Yes, available",
            button_payload="yes_available",
            outcome="confirmed",
        )
    with tenant_context(client_tenant):
        services.record_outcome(booking, "confirmed")
    entries = staff_api.get(f"{URL}/{booking.pk}/timeline").json()
    assert [e["kind"] for e in entries] == [
        "created",
        "attempt",
        "response",
        "outcome_set",
        "job_created",
    ]
    assert entries[2]["data"]["text"] == "Yes, available"
    assert entries[1]["data"]["channel"] == "whatsapp"


def test_confirmed_booking_shows_its_job(staff_api, client_tenant, subscriber):
    booking = make_booking(client_tenant, subscriber, scheduled_date=TOMORROW)
    with tenant_context(client_tenant):
        services.record_outcome(booking, "confirmed")
    job = staff_api.get(f"{URL}/{booking.pk}").json()["job"]
    assert job["assigned_staff_name"] == "Biju K" and job["status"] == "pending"
    with tenant_context(client_tenant):
        assert Job.objects.count() == 1


# --- roles and tenancy ---------------------------------------------------------------------------


def test_support_session_is_read_only(api, super_admin, client_tenant, subscriber):
    from apps.platform_admin.services import start_support_session
    from apps.platform_admin.tenancy import SUPPORT_SESSION_KEY

    make_booking(client_tenant, subscriber)
    sign_in(api, super_admin)
    support = start_support_session(super_admin, client_tenant, "Looking at bookings")
    session = api.session
    session[SUPPORT_SESSION_KEY] = str(support.pk)
    session.save()
    assert api.get(URL).json()["count"] == 1
    assert (
        api.post(URL, payload(subscriber, booking_reference="BKS"), format="json").status_code
        == 403
    )


def test_suspended_tenant_reads_only(staff_api, client_tenant, subscriber):
    from apps.platform_admin.services import suspend_tenant

    booking = make_booking(client_tenant, subscriber)
    suspend_tenant(client_tenant, "Unpaid invoice for October")
    assert staff_api.get(URL).status_code == 200
    response = staff_api.post(
        f"{URL}/{booking.pk}/cancel", {"reason": "Customer cancelled"}, format="json"
    )
    assert response.status_code == 403 and response.json()["code"] == "tenant_suspended"


def test_other_tenants_bookings_are_invisible(staff_api, subscriber):
    other = factories.make_tenant("Elsewhere")
    from apps.industry.seed import seed_gas_agency
    from apps.industry.services import assign_template

    assign_template(other, seed_gas_agency())
    other.save()
    with tenant_context(other):
        their_sub = Subscriber.objects.create(
            tenant=other,
            customer_name="T",
            mobile_number="+919847000009",
            external_ref="T1",
            preferred_language="ml",
        )
    theirs = make_booking(other, their_sub)
    assert staff_api.get(URL).json()["count"] == 0
    assert staff_api.get(f"{URL}/{theirs.pk}").status_code == 404
    assert (
        staff_api.post(
            f"{URL}/{theirs.pk}/cancel", {"reason": "Not mine at all"}, format="json"
        ).status_code
        == 404
    )


# --- bookings import (Workflow 3.5) --------------------------------------------------------------


IMPORTS = "/api/portal/imports"


def upload(api, capture, content):
    with capture(execute=True):
        response = api.post(
            IMPORTS,
            {"entity": "bookings", "file": SimpleUploadedFile("b.csv", content)},
            format="multipart",
        )
    assert response.status_code == 201, response.json()
    return api.get(f"{IMPORTS}/{response.json()['id']}").json()


def confirm(api, capture, job_id):
    with capture(execute=True):
        assert api.post(f"{IMPORTS}/{job_id}/confirm").status_code == 200
    return api.get(f"{IMPORTS}/{job_id}").json()


def csv_bytes(rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "booking_reference",
            "subscriber_ref",
            "booking_date",
            "scheduled_date",
            "cylinder_type",
            "quantity",
            "remarks",
        ]
    )
    writer.writerows(rows)
    return buffer.getvalue().encode()


def test_booking_template_columns(staff_api, subscriber):
    text = staff_api.get(f"{IMPORTS}/template", {"entity": "bookings"}).content.decode().lstrip("﻿")
    assert next(csv.reader(io.StringIO(text))) == [
        "booking_reference",
        "subscriber_ref",
        "booking_date",
        "scheduled_date",
        "cylinder_type",
        "quantity",
        "remarks",
    ]


def test_booking_import_preview_errors(staff_api, subscriber, django_capture_on_commit_callbacks):
    d = TODAY.isoformat()
    job = upload(
        staff_api,
        django_capture_on_commit_callbacks,
        csv_bytes(
            [
                ["BK1", "KL0700001", d, "", "5 kg", "", ""],  # valid; quantity defaults to 1
                ["BK2", "UNKNOWN", d, "", "5 kg", "1", ""],  # unknown subscriber
                [
                    "BK3",
                    "KL0700001",
                    TOMORROW.isoformat(),
                    "",
                    "5 kg",
                    "1",
                    "",
                ],  # booking date in the future
                [
                    "BK4",
                    "KL0700001",
                    d,
                    YESTERDAY.isoformat(),
                    "5 kg",
                    "1",
                    "",
                ],  # delivery date in the past
                ["BK5", "KL0700001", d, "", "", "1", ""],  # cylinder type missing
                ["BK6", "KL0700001", d, "", "5 kg", "0", ""],  # quantity out of range
                ["BK1", "KL0700001", d, "", "5 kg", "1", ""],  # duplicate in the file
            ]
        ),
    )
    assert (job["valid_rows"], job["error_rows"]) == (1, 6)
    assert {e["row"]: list(e["errors"]) for e in job["errors"]} == {
        3: ["subscriber_ref"],
        4: ["booking_date"],
        5: ["scheduled_date"],
        6: ["cylinder_type"],
        7: ["quantity"],
        8: ["booking_reference"],
    }


def test_booking_import_creates_and_updates(
    staff_api, client_tenant, subscriber, hook_calls, django_capture_on_commit_callbacks
):
    existing = make_booking(
        client_tenant, subscriber, booking_reference="BK-OLD", scheduled_date=TOMORROW
    )
    with tenant_context(client_tenant):
        services.record_outcome(existing, "confirmed")
    later = TODAY + dt.timedelta(days=3)
    job = upload(
        staff_api,
        django_capture_on_commit_callbacks,
        csv_bytes(
            [
                [
                    "BK-NEW",
                    "KL0700001",
                    TODAY.isoformat(),
                    TOMORROW.isoformat(),
                    "5 kg",
                    "2",
                    "Gate code 1234",
                ],
                [
                    "BK-OLD",
                    "KL0700001",
                    TODAY.isoformat(),
                    later.isoformat(),
                    "14.2 kg domestic",
                    "1",
                    "",
                ],
            ]
        ),
    )
    assert job["summary"]["would_create"] == 1 and job["summary"]["would_update"] == 1
    job = confirm(staff_api, django_capture_on_commit_callbacks, job["id"])
    assert (job["status"], job["created_rows"], job["updated_rows"]) == ("done", 1, 1)
    with tenant_context(client_tenant):
        new = Schedule.objects.get(booking_reference="BK-NEW")
        assert (
            new.custom_fields == {"cylinder_type": "5 kg", "quantity": 2}
            and new.remarks == "Gate code 1234"
        )
        old = Schedule.objects.get(booking_reference="BK-OLD")
        assert (old.scheduled_date, old.status, old.outcome) == (later, "booked", "")
        assert not Job.objects.filter(schedule=old, status="pending").exists()
        assert list(new.events.values_list("kind", flat=True)) == ["created"]
    assert (None, TOMORROW) in hook_calls and (TOMORROW, later) in hook_calls


def test_timeline_puts_a_reply_before_the_outcome_it_caused(staff_api, client_tenant, subscriber):
    booking = make_booking(client_tenant, subscriber, scheduled_date=TOMORROW)
    moment = timezone.now() - dt.timedelta(minutes=5)
    with tenant_context(client_tenant):
        call = Attempt.objects.create(
            tenant=client_tenant,
            schedule=booking,
            channel="voice",
            sent_at=moment,
            delivery_status="answered",
        )
        Response.objects.create(
            tenant=client_tenant,
            attempt=call,
            received_at=moment,
            outcome="confirmed",
            transcript="Customer: Yes",
        )
        ScheduleEvent.objects.create(
            tenant=client_tenant,
            schedule=booking,
            kind="outcome_set",
            occurred_at=moment,
            data={"outcome": "confirmed"},
        )
    kinds = [e["kind"] for e in staff_api.get(f"{URL}/{booking.pk}/timeline").json()]
    assert kinds == ["attempt", "response", "outcome_set"]
