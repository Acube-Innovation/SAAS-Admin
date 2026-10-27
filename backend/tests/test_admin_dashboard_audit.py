"""Dashboard tiles (spec 2.2) and the Audit log screen (spec 2.8): filters, facets, CSV export."""

import csv
import datetime as dt
import io
from unittest import mock

import pytest
from django.utils import timezone

from apps.audit.api import IST, safe_cell
from apps.audit.models import Action, AuditLog
from apps.core.models import Tenant
from apps.core.tenancy import db_context, tenant_context
from apps.industry.services import assign_template
from apps.messaging_templates import services as template_services
from apps.messaging_templates.models import VersionStatus, WhatsAppTemplateVersion
from apps.messaging_templates.providers import StubProvider
from apps.platform_admin import services as admin_services
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_api(api, super_admin):
    sign_in(api, super_admin)
    return api


def entry(action, *, tenant=None, actor=None, at=None, **extra):
    when = at or timezone.now()
    with mock.patch("django.utils.timezone.now", return_value=when):
        from apps.audit.services import write_audit

        write_audit(action, tenant=tenant, actor=actor, **extra)


def ist(y, m, d, hh=12, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=IST)


def results(response):
    assert response.status_code == 200, response.json()
    return response.json()["results"]


def export_rows(response):
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    return list(csv.DictReader(io.StringIO(response.content.decode())))


# --- permissions ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/api/admin/dashboard",
        "/api/admin/audit-logs",
        "/api/admin/audit-logs/facets",
        "/api/admin/audit-logs/export",
    ],
)
def test_only_platform_staff(api, owner, url):
    assert api.get(url).status_code in (401, 403)
    sign_in(api, owner)
    assert api.get(url).status_code == 403


def test_support_staff_can_read_both_screens(api, support_user):
    sign_in(api, support_user)
    assert api.get("/api/admin/dashboard").status_code == 200
    assert api.get("/api/admin/audit-logs").status_code == 200


# --- dashboard ------------------------------------------------------------------------------------


def test_dashboard_tiles(admin_api):
    data = admin_api.get("/api/admin/dashboard").json()

    with db_context(platform=True):
        assert data["clients_active"] == Tenant.objects.filter(status="active").count()
        assert data["clients_suspended"] == Tenant.objects.filter(status="suspended").count()
    # Message and webhook tiles read zero until the Phase 2 engine; change requests until A1.
    assert data["messages_today"] == {"sent": 0, "delivered": 0, "failed": 0, "replied": 0}
    assert data["messages_live"] is False
    assert data["failed_webhooks_24h"] == 0
    assert data["open_change_requests"] == 0
    assert data["quality_rating"] == "GREEN"
    assert data["quality_alert"] is False


def test_dashboard_counts_suspended_clients(admin_api, tenant):
    before = admin_api.get("/api/admin/dashboard").json()
    admin_services.suspend_tenant(tenant, "Unpaid invoice for October")
    after = admin_api.get("/api/admin/dashboard").json()
    assert after["clients_active"] == before["clients_active"] - 1
    assert after["clients_suspended"] == before["clients_suspended"] + 1


def test_dashboard_counts_templates_awaiting_action(admin_api, super_admin, tenant, gas_template):
    assign_template(tenant, gas_template)
    tenant.save()
    delivery = gas_template.schedule_types.get(key="cylinder_delivery")
    S = VersionStatus
    for n, status in enumerate([S.DRAFT, S.SIGNED_OFF, S.SENT_BACK, S.REJECTED, S.APPROVED]):
        version = template_services.create_template(
            tenant,
            {
                "name": f"reminder_{n}",
                "language": "en",
                "schedule_type": delivery,
                "body": "Hello {{customer_name}}, your cylinder arrives on {{scheduled_date}}.",
                "footer": "",
                "buttons": [{"text": "Yes, available", "outcome": "confirmed"}],
                "sample_values": {"customer_name": "Asha", "scheduled_date": "28 Oct 2026"},
            },
            super_admin,
        )
        with tenant_context(tenant):
            WhatsAppTemplateVersion.objects.filter(pk=version.pk).update(status=status)

    # signed off (waiting to be submitted), sent back and rejected need someone to act.
    assert admin_api.get("/api/admin/dashboard").json()["templates_awaiting_action"] == 3


def test_dashboard_counts_active_support_sessions(admin_api, super_admin, tenant):
    assert admin_api.get("/api/admin/dashboard").json()["active_support_sessions"] == 0
    admin_services.start_support_session(super_admin, tenant, "Checking the template set-up")
    assert admin_api.get("/api/admin/dashboard").json()["active_support_sessions"] == 1


@pytest.mark.parametrize(("rating", "alert"), [("GREEN", False), ("YELLOW", False), ("RED", True)])
def test_dashboard_quality_rating(admin_api, rating, alert):
    StubProvider.set_quality(rating)
    data = admin_api.get("/api/admin/dashboard").json()
    assert data["quality_rating"] == rating
    assert data["quality_alert"] is alert


def test_dashboard_quality_unknown_when_provider_fails(admin_api):
    with mock.patch.object(StubProvider, "get_quality_rating", side_effect=RuntimeError("down")):
        assert admin_api.get("/api/admin/dashboard").json()["quality_rating"] == "UNKNOWN"


# --- audit log list and filters ---------------------------------------------------------------


@pytest.fixture
def entries(super_admin, support_user, tenant):
    """A small, known log: two clients, two actors, three actions over three India-time days."""
    other = factories.make_tenant("Thrissur Gas")
    entry(
        Action.CREATE,
        tenant=tenant,
        actor=super_admin,
        at=ist(2026, 10, 20, 0, 5),
        object_type="core.tenant",
        object_id=str(tenant.pk),
        after={"name": "Kochi Gas Agency"},
    )
    entry(
        Action.UPDATE,
        tenant=tenant,
        actor=super_admin,
        at=ist(2026, 10, 21, 23, 55),
        object_type="core.tenant",
        object_id=str(tenant.pk),
        before={"city": "Kochi"},
        after={"city": "Ernakulam"},
    )
    entry(
        Action.STATUS_CHANGE,
        tenant=other,
        actor=support_user,
        at=ist(2026, 10, 22, 9),
        object_type="core.tenant",
        object_id=str(other.pk),
        before={"status": "active"},
        after={"status": "suspended"},
    )
    entry(
        Action.CREATE,
        tenant=other,
        actor=support_user,
        at=ist(2026, 10, 22, 10),
        object_type="accounts.membership",
        object_id="42",
        after={"role": "manager"},
    )
    return {"tenant": tenant, "other": other}


def ids(rows):
    return [(r["action"], r["object_type"]) for r in rows]


def test_list_is_newest_first_with_tenant_names(admin_api, entries):
    rows = results(
        admin_api.get("/api/admin/audit-logs", {"date_from": "2026-10-20", "date_to": "2026-10-22"})
    )
    assert ids(rows) == [
        ("create", "accounts.membership"),
        ("status_change", "core.tenant"),
        ("update", "core.tenant"),
        ("create", "core.tenant"),
    ]
    assert rows[1]["tenant_name"] == "Thrissur Gas"
    assert rows[2]["before"] == {"city": "Kochi"}
    assert rows[2]["after"] == {"city": "Ernakulam"}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"action": "create"}, [("create", "accounts.membership"), ("create", "core.tenant")]),
        ({"object_type": "accounts.membership"}, [("create", "accounts.membership")]),
        (
            {"actor": "SUPPORT@"},
            [("create", "accounts.membership"), ("status_change", "core.tenant")],
        ),
        # India-time day boundaries: 00:05 on the 20th and 23:55 on the 21st both count.
        ({"date_from": "2026-10-20", "date_to": "2026-10-20"}, [("create", "core.tenant")]),
        ({"date_from": "2026-10-21", "date_to": "2026-10-21"}, [("update", "core.tenant")]),
        (
            {"date_from": "2026-10-21", "date_to": "2026-10-22"},
            [
                ("create", "accounts.membership"),
                ("status_change", "core.tenant"),
                ("update", "core.tenant"),
            ],
        ),
        (
            {"action": "create", "actor": "admin@", "date_to": "2026-10-22"},
            [("create", "core.tenant")],
        ),
    ],
)
def test_filters_return_the_matching_entries(admin_api, entries, params, expected):
    rows = results(admin_api.get("/api/admin/audit-logs", {"date_to": "2026-10-22", **params}))
    assert ids(rows) == expected


def test_tenant_filter(admin_api, entries):
    rows = results(admin_api.get("/api/admin/audit-logs", {"tenant": str(entries["other"].pk)}))
    assert ids(rows) == [("create", "accounts.membership"), ("status_change", "core.tenant")]
    assert {r["tenant_id"] for r in rows} == {str(entries["other"].pk)}


def test_bad_date_is_a_validation_error(admin_api):
    response = admin_api.get("/api/admin/audit-logs", {"date_from": "22-10-2026"})
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_page_size_is_capped(admin_api, entries):
    response = admin_api.get("/api/admin/audit-logs", {"page_size": 2})
    assert len(response.json()["results"]) == 2
    assert response.json()["next"]
    response = admin_api.get("/api/admin/audit-logs", {"page_size": 10_000})
    assert response.status_code == 200


def test_facets_list_known_actions_and_records(admin_api, entries):
    data = admin_api.get("/api/admin/audit-logs/facets").json()
    assert {"create", "update", "status_change"} <= set(data["actions"])
    assert {"core.tenant", "accounts.membership"} <= set(data["object_types"])
    assert data["actions"] == sorted(data["actions"])
    names = [t["name"] for t in data["tenants"]]
    assert {"Kochi Gas Agency", "Thrissur Gas"} <= set(names)
    assert names == sorted(names)


# --- CSV export -------------------------------------------------------------------------------


def test_csv_export_matches_the_on_screen_rows(admin_api, entries):
    filters = {"date_from": "2026-10-20", "date_to": "2026-10-22", "action": "create"}
    on_screen = results(admin_api.get("/api/admin/audit-logs", {**filters, "page_size": 200}))
    exported = export_rows(admin_api.get("/api/admin/audit-logs/export", filters))

    assert len(exported) == len(on_screen) == 2
    for row, shown in zip(exported, on_screen, strict=True):
        assert row["actor_email"] == shown["actor_email"]
        assert row["action"] == shown["action"]
        assert row["object_type"] == shown["object_type"]
        assert row["object_id"] == shown["object_id"]
        assert row["tenant"] == shown["tenant_name"]
        assert dt.datetime.fromisoformat(row["created_at"]) == dt.datetime.fromisoformat(
            shown["created_at"].replace("Z", "+00:00")
        )


def test_csv_export_includes_before_and_after(admin_api, entries):
    rows = export_rows(admin_api.get("/api/admin/audit-logs/export", {"action": "update"}))
    assert rows[0]["before"] == '{"city": "Kochi"}'
    assert rows[0]["after"] == '{"city": "Ernakulam"}'
    assert rows[0]["created_at"] == "2026-10-21T23:55:00+05:30"


def test_csv_export_is_itself_audited(admin_api, super_admin, entries):
    response = admin_api.get("/api/admin/audit-logs/export", {"action": "create", "bogus": "x"})
    assert "attachment; filename=" in response["Content-Disposition"]
    with db_context(platform=True):
        exports = list(AuditLog.objects.filter(action=Action.EXPORT))
    assert len(exports) == 1
    assert exports[0].actor_id == super_admin.pk
    assert exports[0].tenant_id is None
    assert exports[0].object_type == "audit.auditlog"
    assert exports[0].after == {"filters": {"action": "create"}, "rows": 2}


def test_csv_export_refuses_huge_exports(admin_api, entries):
    with mock.patch("apps.audit.api.EXPORT_LIMIT", 1):
        response = admin_api.get("/api/admin/audit-logs/export")
    assert response.status_code == 400
    assert response.json()["code"] == "export_too_large"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=SUM(A1)", "'=SUM(A1)"),
        ("+91", "'+91"),
        ("-1", "'-1"),
        ("@x", "'@x"),
        ("plain", "plain"),
        (None, ""),
    ],
)
def test_csv_cells_cannot_run_formulas(value, expected):
    assert safe_cell(value) == expected
