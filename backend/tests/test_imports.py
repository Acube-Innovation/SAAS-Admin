"""CSV import engine (Workflow 3.5) with the subscriber adapter."""

import csv
import io
import time
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.tenancy import db_context, tenant_context
from apps.industry.models import FieldDefinition
from apps.subscribers import importer
from apps.subscribers.models import Area, ImportJob, Subscriber
from tests import factories

pytestmark = pytest.mark.django_db

URL = "/api/portal/imports"
HEADER = [
    "customer_name",
    "mobile_number",
    "alternate_number",
    "external_ref",
    "preferred_language",
    "address",
    "area",
    "whatsapp_consent",
    "consent_date",
    "consent_source",
    "status",
]


def csv_bytes(rows, header=HEADER, bom=False):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    text = buffer.getvalue()
    return (("﻿" if bom else "") + text).encode()


def row(ref="KL1", **overrides):
    values = {
        "customer_name": "Asha Menon",
        "mobile_number": "9847012345",
        "alternate_number": "",
        "external_ref": ref,
        "preferred_language": "ml",
        "address": "MG Road",
        "area": "Edappally",
        "whatsapp_consent": "yes",
        "consent_date": "",
        "consent_source": "",
        "status": "",
    }
    values.update(overrides)
    return [values[h] for h in HEADER]


@pytest.fixture
def staff_api(portal):
    return portal("office_staff")


@pytest.fixture(autouse=True)
def areas(client_tenant):
    with tenant_context(client_tenant):
        Area.objects.create(tenant=client_tenant, name="Edappally", code="EDP")
        Area.objects.create(tenant=client_tenant, name="Old Route", is_active=False)


@pytest.fixture
def upload(staff_api, django_capture_on_commit_callbacks):
    """Upload a CSV and run the preview job (eager Celery, after commit). Returns the job JSON."""

    def do(content, name="subscribers.csv"):
        with django_capture_on_commit_callbacks(execute=True):
            response = staff_api.post(
                URL,
                {"entity": "subscribers", "file": SimpleUploadedFile(name, content, "text/csv")},
                format="multipart",
            )
        assert response.status_code == 201, response.json()
        return staff_api.get(f"{URL}/{response.json()['id']}").json()

    return do


@pytest.fixture
def confirm(staff_api, django_capture_on_commit_callbacks):
    def do(job_id):
        with django_capture_on_commit_callbacks(execute=True):
            response = staff_api.post(f"{URL}/{job_id}/confirm")
        assert response.status_code == 200, response.json()
        return staff_api.get(f"{URL}/{job_id}").json()

    return do


def subscribers(tenant):
    with tenant_context(tenant):
        return {s.external_ref: s for s in Subscriber.objects.select_related("area")}


# --- template -------------------------------------------------------------------------------


def test_template_columns_come_from_the_industry_template(staff_api, gas_template):
    FieldDefinition.objects.create(
        template=gas_template,
        entity="subscriber",
        key="ration_card",
        label="Ration card",
        field_type="text",
        is_core=False,
        order=50,
    )
    FieldDefinition.objects.create(
        template=gas_template,
        entity="subscriber",
        key="retired",
        label="Retired",
        field_type="text",
        is_core=False,
        is_active=False,
        order=51,
    )
    response = staff_api.get(f"{URL}/template", {"entity": "subscribers"})
    assert response["Content-Type"].startswith("text/csv")
    text = response.content.decode()
    assert text.startswith("﻿")
    assert next(csv.reader(io.StringIO(text.lstrip("﻿")))) == [*HEADER, "ration_card"]


def test_unknown_entity(staff_api):
    assert staff_api.get(f"{URL}/template", {"entity": "invoices"}).status_code == 400


# --- preview --------------------------------------------------------------------------------


def test_preview_counts_and_saves_nothing(upload, client_tenant):
    job = upload(csv_bytes([row("KL1"), row("KL2"), row("KL3", mobile_number="123")]))
    assert job["status"] == "previewed"
    assert (job["total_rows"], job["valid_rows"], job["error_rows"]) == (3, 2, 1)
    assert job["summary"]["would_create"] == 2 and job["summary"]["would_update"] == 0
    assert job["has_error_file"] is True
    assert subscribers(client_tenant) == {}


@pytest.mark.parametrize(
    ("overrides", "column"),
    [
        ({"customer_name": ""}, "customer_name"),
        ({"customer_name": "x" * 101}, "customer_name"),
        ({"mobile_number": "12345"}, "mobile_number"),
        ({"alternate_number": "999"}, "alternate_number"),
        ({"external_ref": ""}, "external_ref"),
        ({"preferred_language": "french"}, "preferred_language"),
        ({"address": "x" * 501}, "address"),
        ({"area": "Atlantis"}, "area"),
        ({"area": "Old Route"}, "area"),
        ({"whatsapp_consent": ""}, "whatsapp_consent"),
        ({"whatsapp_consent": "perhaps"}, "whatsapp_consent"),
        ({"consent_date": "31/02/2026"}, "consent_date"),
        ({"consent_date": "01-01-2099"}, "consent_date"),
        ({"consent_source": "carrier pigeon"}, "consent_source"),
        ({"status": "gone"}, "status"),
    ],
)
def test_preview_reports_each_row_error(upload, overrides, column):
    job = upload(csv_bytes([row("KL1", **overrides)]))
    assert job["error_rows"] == 1
    assert job["errors"][0]["row"] == 2
    assert column in job["errors"][0]["errors"]


def test_duplicate_reference_in_the_file(upload):
    job = upload(csv_bytes([row("KL1"), row("KL2"), row("KL1", customer_name="Again")]))
    assert job["valid_rows"] == 2
    assert job["errors"] == [
        {
            "row": 4,
            "key": "KL1",
            "errors": {"external_ref": "Duplicate external_ref in this file (first on row 2)."},
        }
    ]


def test_flexible_values_are_accepted(upload, confirm, client_tenant):
    job = upload(
        csv_bytes(
            [
                row(
                    "KL1",
                    mobile_number="+91 98470-12345",
                    preferred_language="Malayalam",
                    area="edp",
                    whatsapp_consent="Y",
                    consent_date="05/03/2025",
                    consent_source="Paper form",
                    status="Active",
                ),
                row("KL2", preferred_language="", whatsapp_consent="no", area=""),
            ]
        )
    )
    assert job["error_rows"] == 0, job["errors"]
    confirm(job["id"])
    one, two = subscribers(client_tenant)["KL1"], subscribers(client_tenant)["KL2"]
    assert one.mobile_number == "+919847012345" and one.preferred_language == "ml"
    assert one.area.name == "Edappally" and str(one.consent_date) == "2025-03-05"
    assert one.consent_source == "paper_form"
    assert two.preferred_language == client_tenant.default_language and two.area is None
    assert two.whatsapp_consent is False and two.consent_date is None


def test_consent_without_date_defaults_to_import_day_and_source(upload, confirm, client_tenant):
    confirm(upload(csv_bytes([row("KL1")]))["id"])
    subscriber = subscribers(client_tenant)["KL1"]
    assert subscriber.consent_date == timezone.localdate()
    assert subscriber.consent_source == "import"


def test_headers_match_labels_and_ignore_unknown_columns(upload):
    header = ["Customer name", "Mobile Number", "Consumer number", "WhatsApp consent", "Notes"]
    job = upload(
        csv_bytes([["Asha", "9847012345", "KL1", "yes", "ignore me"]], header=header, bom=True)
    )
    assert job["valid_rows"] == 1
    assert job["summary"]["ignored_columns"] == ["Notes"]


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"customer_name,external_ref\nAsha,KL1\n", "Missing required columns"),
        (csv_bytes([row()], header=[*HEADER[:3], "external_ref", *HEADER[3:]]), "appears twice"),
        ("customer_name,mobile_number\nAsha Mén".encode("latin-1"), "not UTF-8"),
    ],
)
def test_whole_file_problems_fail_the_job(upload, content, reason):
    job = upload(content)
    assert job["status"] == "failed"
    assert reason in job["failure_reason"]


def test_empty_upload_is_refused(staff_api):
    response = staff_api.post(
        URL, {"entity": "subscribers", "file": SimpleUploadedFile("a.csv", b"")}, format="multipart"
    )
    assert response.status_code == 400 and "file" in response.json()["fields"]


def test_header_only_file_has_no_rows(upload):
    job = upload(csv_bytes([]))
    assert job["status"] == "previewed" and job["total_rows"] == 0


def test_row_limit(upload, monkeypatch):
    monkeypatch.setattr(importer, "MAX_ROWS", 3)
    job = upload(csv_bytes([row(f"KL{n}") for n in range(4)]))
    assert job["status"] == "failed" and "more than 3 rows" in job["failure_reason"]


def test_upload_must_be_a_small_csv(staff_api, monkeypatch):
    response = staff_api.post(
        URL,
        {"entity": "subscribers", "file": SimpleUploadedFile("x.xlsx", b"x")},
        format="multipart",
    )
    assert response.status_code == 400 and "file" in response.json()["fields"]
    monkeypatch.setattr(importer, "MAX_BYTES", 10)
    big = SimpleUploadedFile("x.csv", b"x" * 11)
    response = staff_api.post(URL, {"entity": "subscribers", "file": big}, format="multipart")
    assert response.status_code == 400 and "20 MB" in response.json()["fields"]["file"][0]


def test_blank_lines_are_skipped(upload):
    content = (
        csv_bytes([row("KL1")]) + b"\n,,,,,,,,,,\n" + csv_bytes([row("KL2")])[len(csv_bytes([])) :]
    )
    job = upload(content)
    assert (job["total_rows"], job["valid_rows"]) == (2, 2)


def test_custom_fields_are_validated_and_imported(upload, confirm, client_tenant, gas_template):
    FieldDefinition.objects.create(
        template=gas_template,
        entity="subscriber",
        key="family_size",
        label="Family size",
        field_type="number",
        required=True,
        is_core=False,
        order=50,
    )
    header = [*HEADER, "family_size"]
    job = upload(
        csv_bytes([[*row("KL1"), "4"], [*row("KL2"), "many"], [*row("KL3"), ""]], header=header)
    )
    assert job["valid_rows"] == 1
    assert {e["row"]: list(e["errors"]) for e in job["errors"]} == {
        3: ["family_size"],
        4: ["family_size"],
    }
    confirm(job["id"])
    assert subscribers(client_tenant)["KL1"].custom_fields == {"family_size": 4}


# --- error file ------------------------------------------------------------------------------


def test_error_file_has_original_columns_plus_error(staff_api, upload):
    job = upload(csv_bytes([row("KL1"), row("KL2", mobile_number="12", area="Atlantis")]))
    response = staff_api.get(f"{URL}/{job['id']}/error-file")
    assert response["Content-Disposition"] == 'attachment; filename="subscribers-errors.csv"'
    rows = list(csv.reader(io.StringIO(response.content.decode().lstrip("﻿"))))
    assert rows[0] == [*HEADER, "error"]
    assert len(rows) == 2
    assert rows[1][:-1] == row("KL2", mobile_number="12", area="Atlantis")
    assert "mobile_number:" in rows[1][-1] and "area: Unknown area." in rows[1][-1]


def test_no_error_file_when_every_row_is_valid(staff_api, upload):
    job = upload(csv_bytes([row("KL1")]))
    assert job["has_error_file"] is False
    assert staff_api.get(f"{URL}/{job['id']}/error-file").status_code == 404


# --- confirm ---------------------------------------------------------------------------------


def test_confirm_upserts_by_reference_and_skips_error_rows(upload, confirm, client_tenant):
    with tenant_context(client_tenant):
        Subscriber.objects.create(
            tenant=client_tenant,
            customer_name="Old Name",
            mobile_number="+919847000001",
            external_ref="KL1",
            preferred_language="en",
            address="Keep me",
            opted_out=True,
            custom_fields={"kept": 1},
        )
    job = upload(
        csv_bytes(
            [
                row("KL1", customer_name="New Name", address="", preferred_language=""),
                row("KL2"),
                row("KL3", mobile_number="bad"),
            ]
        )
    )
    assert job["summary"] == {"would_create": 1, "would_update": 1, "ignored_columns": []}
    job = confirm(job["id"])
    assert job["status"] == "done"
    assert (job["created_rows"], job["updated_rows"], job["error_rows"]) == (1, 1, 1)
    rows = subscribers(client_tenant)
    assert set(rows) == {"KL1", "KL2"}
    updated = rows["KL1"]
    assert updated.customer_name == "New Name"
    assert updated.address == "Keep me"  # blank cell: unchanged
    assert updated.preferred_language == "en"
    assert updated.opted_out is True  # an import never clears an opt-out
    assert updated.custom_fields == {"kept": 1}


def test_confirm_is_audited_with_counts(upload, confirm, client_tenant):
    job = confirm(upload(csv_bytes([row("KL1"), row("KL2", mobile_number="x")]))["id"])
    with db_context(platform=True):
        entry = AuditLog.objects.get(action="import", object_id=job["id"])
    assert entry.tenant_id == client_tenant.pk
    assert entry.after == {
        "entity": "subscribers",
        "file": "subscribers.csv",
        "total_rows": 2,
        "created": 1,
        "updated": 0,
        "skipped": 1,
    }
    assert entry.actor_email.endswith("@agency.test")


def test_confirm_only_once_and_only_after_preview(
    staff_api, upload, confirm, django_capture_on_commit_callbacks
):
    job = upload(csv_bytes([row("KL1")]))
    confirm(job["id"])
    response = staff_api.post(f"{URL}/{job['id']}/confirm")
    assert response.status_code == 409 and response.json()["code"] == "invalid_state"


def test_nothing_to_confirm_when_every_row_failed(staff_api, upload):
    job = upload(csv_bytes([row("KL1", mobile_number="x")]))
    response = staff_api.post(f"{URL}/{job['id']}/confirm")
    assert response.status_code == 409 and response.json()["code"] == "nothing_to_import"


def test_confirm_revalidates_rows(upload, confirm, client_tenant):
    job = upload(csv_bytes([row("KL1"), row("KL2", area="Edappally")]))
    with tenant_context(client_tenant):
        Area.objects.filter(name="Edappally").update(is_active=False)
    job = confirm(job["id"])
    assert (job["created_rows"], job["error_rows"]) == (0, 2)
    assert subscribers(client_tenant) == {}


def test_a_failure_during_confirm_saves_nothing(upload, confirm, client_tenant):
    job = upload(csv_bytes([row("KL1"), row("KL2")]))
    with mock.patch.object(
        importer.SubscriberAdapter, "upsert", side_effect=RuntimeError("db down")
    ):
        job = confirm(job["id"])
    assert job["status"] == "failed" and "Nothing was saved" in job["failure_reason"]
    assert subscribers(client_tenant) == {}


# --- tenancy and roles -----------------------------------------------------------------------


def test_jobs_are_tenant_scoped(staff_api, upload, client_tenant):
    job = upload(csv_bytes([row("KL1")]))
    other = factories.make_tenant("Elsewhere")
    with tenant_context(other):
        assert not ImportJob.objects.filter(pk=job["id"]).exists()
    assert [j["id"] for j in staff_api.get(URL).json()["results"]] == [job["id"]]


def test_other_tenants_job_cannot_be_confirmed(portal, upload):
    job = upload(csv_bytes([row("KL1")]))
    other = factories.make_tenant("Elsewhere")
    intruder = portal("owner", tenant=other)
    assert intruder.post(f"{URL}/{job['id']}/confirm").status_code == 404
    assert intruder.get(f"{URL}/{job['id']}/error-file").status_code == 404


def test_support_session_cannot_import(api, super_admin, client_tenant):
    from apps.platform_admin.services import start_support_session
    from apps.platform_admin.tenancy import SUPPORT_SESSION_KEY
    from tests.conftest import sign_in

    sign_in(api, super_admin)
    support = start_support_session(super_admin, client_tenant, "Looking at an import")
    session = api.session
    session[SUPPORT_SESSION_KEY] = str(support.pk)
    session.save()
    assert api.get(URL).status_code == 200
    response = api.post(
        URL,
        {"entity": "subscribers", "file": SimpleUploadedFile("a.csv", csv_bytes([row()]))},
        format="multipart",
    )
    assert response.status_code == 403


# --- scale (Done when: a 20,000-row file previews and confirms correctly) ---------------------


def test_twenty_thousand_rows_preview_and_confirm(upload, confirm, client_tenant):
    rows = [
        row(
            f"KL{n:06}",
            customer_name=f"Customer {n}",
            mobile_number=f"9{n:09d}",
            area="Edappally" if n % 2 else "",
        )
        for n in range(20_000)
    ]
    rows[123] = row("KL000123", mobile_number="12")  # one bad row
    rows[19_999] = row("KL000001")  # one duplicate of row 3 of the file
    with tenant_context(client_tenant):
        Subscriber.objects.bulk_create(
            [
                Subscriber(
                    tenant=client_tenant,
                    customer_name="Before",
                    mobile_number="+919847000001",
                    external_ref=f"KL{n:06}",
                    preferred_language="en",
                )
                for n in range(500)
            ]
        )

    started = time.monotonic()
    job = upload(csv_bytes(rows))
    preview_seconds = time.monotonic() - started
    assert job["status"] == "previewed"
    assert (job["total_rows"], job["valid_rows"], job["error_rows"]) == (20_000, 19_998, 2)
    assert job["summary"]["would_create"] == 19_998 - 499 and job["summary"]["would_update"] == 499
    assert {e["row"] for e in job["errors"]} == {125, 20_001}

    started = time.monotonic()
    job = confirm(job["id"])
    confirm_seconds = time.monotonic() - started
    assert job["status"] == "done"
    assert (job["created_rows"], job["updated_rows"], job["error_rows"]) == (19_499, 499, 2)

    with tenant_context(client_tenant):
        assert Subscriber.objects.count() == 19_998 + 1  # KL000123 existed before, row was bad
        assert Subscriber.objects.get(external_ref="KL000002").customer_name == "Customer 2"
        assert Subscriber.objects.get(external_ref="KL000123").customer_name == "Before"
        assert (
            Subscriber.objects.filter(area__name="Edappally").count() == 9_998
        )  # odd rows; 2 excluded
    assert preview_seconds < 60 and confirm_seconds < 90, (preview_seconds, confirm_seconds)


def test_stuck_imports_are_failed_after_30_minutes(client_tenant, staff_api):
    from datetime import timedelta

    from django.utils import timezone

    with tenant_context(client_tenant):
        stuck = ImportJob.objects.create(
            tenant=client_tenant,
            entity="subscribers",
            original_filename="a.csv",
            status="previewing",
        )
        recent = ImportJob.objects.create(
            tenant=client_tenant,
            entity="subscribers",
            original_filename="b.csv",
            status="confirming",
        )
        ImportJob.objects.filter(pk=stuck.pk).update(
            updated_at=timezone.now() - timedelta(minutes=31)
        )
    other = factories.make_tenant("Elsewhere")
    with tenant_context(other):
        theirs = ImportJob.objects.create(
            tenant=other, entity="subscribers", original_filename="c.csv", status="confirming"
        )
        ImportJob.objects.filter(pk=theirs.pk).update(
            updated_at=timezone.now() - timedelta(hours=2)
        )

    assert importer.fail_stale_imports() == 2
    with tenant_context(client_tenant):
        stuck.refresh_from_db()
        recent.refresh_from_db()
    assert stuck.status == "failed" and "Upload the file again" in stuck.failure_reason
    assert recent.status == "confirming"
