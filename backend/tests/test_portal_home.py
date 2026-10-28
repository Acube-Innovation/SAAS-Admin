"""Portal config (role, status), Home (spec 2.1) and the first-login checklist (Workflow 3.6)."""

import datetime as dt
import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import Membership
from apps.audit.models import AuditLog
from apps.core.models import Tenant
from apps.core.tenancy import db_context, tenant_context
from apps.jobs.models import FollowUpItem
from apps.schedules.models import Attempt, Schedule
from apps.subscribers.models import Area
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db
TOMORROW = timezone.localdate() + dt.timedelta(days=1)


@pytest.fixture
def demo(gas_template, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("demo-password-for-tests-1\n"))
    call_command(
        "seed_demo",
        "--allow-non-debug",
        "--password-stdin",
        "--subscribers",
        "150",
        stdout=io.StringIO(),
    )
    return Tenant.objects.get(name="Demo Gas Agency")


def as_role(api, tenant, role):
    user = factories.make_user(f"{role}-{factories.next_id()}@demo.test")
    factories.add_membership(user, tenant, role)
    sign_in(api, user)
    return api


# --- config -------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["owner", "manager", "office_staff"])
def test_config_tells_the_portal_the_role_and_status(api, demo, role):
    data = as_role(api, demo, role).get("/api/portal/config").json()
    assert data["role"] == role
    assert data["tenant_status"] == "active" and data["tenant_name"] == "Demo Gas Agency"
    labels = {f["key"]: f["label"] for f in data["subscriber_fields"]}
    assert labels["external_ref"] == "Consumer number"  # labels come from the industry template


def test_config_for_a_suspended_agency(api, demo):
    from apps.platform_admin.services import suspend_tenant

    suspend_tenant(demo, "Unpaid invoice for October")
    data = as_role(api, demo, "manager").get("/api/portal/config").json()
    assert data["tenant_status"] == "suspended"
    assert data["suspension_reason"] == "Unpaid invoice for October" and data["suspended_at"]


def test_config_in_a_support_session(api, super_admin, demo):
    from apps.platform_admin.services import start_support_session
    from apps.platform_admin.tenancy import SUPPORT_SESSION_KEY

    sign_in(api, super_admin)
    support = start_support_session(super_admin, demo, "Walking the client through Home")
    session = api.session
    session[SUPPORT_SESSION_KEY] = str(support.pk)
    session.save()
    assert api.get("/api/portal/config").json()["role"] == "support"
    assert api.get("/api/portal/home").json()["checklist"] is None


# --- Home shows the seeded numbers ---------------------------------------------------------------


def test_home_numbers_match_the_seeded_data(api, demo):
    data = as_role(api, demo, "office_staff").get("/api/portal/home").json()
    with tenant_context(demo):
        live = Schedule.objects.exclude(status="cancelled")
        tomorrow = live.filter(scheduled_date=TOMORROW)
        counts = data["tomorrow_counts"]
        assert counts["total"] == tomorrow.count() > 0
        for outcome in ["confirmed", "reschedule", "not_available", "no_response", "needs_review"]:
            assert counts[outcome] == tomorrow.filter(outcome=outcome).count(), outcome
        assert (
            counts["awaiting_reply"] == tomorrow.filter(outcome="", status="awaiting_reply").count()
        )
        assert data["no_date"] == live.filter(scheduled_date=None).count() > 0
        queue = FollowUpItem.objects.exclude(status="resolved")
        assert data["follow_up"]["open"] == queue.count() > 0
        oldest = min(item.opened_at for item in queue)
        expected = int((timezone.now() - oldest).total_seconds() // 60)
        assert abs(data["follow_up"]["oldest_minutes"] - expected) <= 1
        assert (
            data["sent_today"]
            == Attempt.objects.filter(
                sent_at__date=timezone.localdate(), channel="whatsapp"
            ).count()
        )
    assert data["tomorrow"] == TOMORROW.isoformat()


def test_home_alerts(api, demo):
    api = as_role(api, demo, "manager")
    codes = {a["code"] for a in api.get("/api/portal/home").json()["alerts"]}
    assert codes == set()  # the demo agency's reminder template is live

    from apps.messaging_templates.models import WhatsAppTemplateVersion

    with tenant_context(demo):
        WhatsAppTemplateVersion.objects.filter(tenant=demo).update(is_live=False)
    codes = {a["code"] for a in api.get("/api/portal/home").json()["alerts"]}
    assert codes == {"no_live_template"}

    demo.daily_send_cap = 1
    demo.save()
    codes = {a["code"] for a in api.get("/api/portal/home").json()["alerts"]}
    assert "send_cap_reached" in codes

    from apps.platform_admin.services import suspend_tenant

    suspend_tenant(demo, "Unpaid invoice for October")
    alerts = api.get("/api/portal/home").json()["alerts"]
    assert {"code": "tenant_suspended", "severity": "error"} in alerts


def test_home_paused_template_alert(api, client_tenant, gas_template, super_admin):
    from apps.messaging_templates import services as template_services
    from apps.messaging_templates.models import WhatsAppTemplateVersion

    delivery = gas_template.schedule_types.get(key="cylinder_delivery")
    version = template_services.create_template(
        client_tenant,
        {
            "name": "delivery_reminder",
            "language": "en",
            "schedule_type": delivery,
            "body": "Hello {{customer_name}}, delivery on {{scheduled_date}}.",
            "footer": "",
            "buttons": [{"text": "Yes, available", "outcome": "confirmed"}],
            "sample_values": {"customer_name": "Asha", "scheduled_date": "28 Oct"},
        },
        super_admin,
    )
    with tenant_context(client_tenant):
        WhatsAppTemplateVersion.objects.filter(pk=version.pk).update(status="paused")
    codes = {
        a["code"]
        for a in as_role(api, client_tenant, "owner").get("/api/portal/home").json()["alerts"]
    }
    assert "template_paused" in codes


# --- first-login checklist ------------------------------------------------------------------------


def items(data):
    return {item["key"]: item for item in data["checklist"]["items"]}


def test_checklist_is_for_the_owner_only(api, demo):
    assert as_role(api, demo, "manager").get("/api/portal/home").json()["checklist"] is None
    response = api.post(
        "/api/portal/checklist", {"item": "agency_details", "action": "done"}, format="json"
    )
    assert response.status_code == 403


def test_checklist_ticks_facts_and_manual_items(api, client_tenant):
    api = as_role(api, client_tenant, "owner")
    data = api.get("/api/portal/home").json()
    assert [i["key"] for i in data["checklist"]["items"]] == [
        "agency_details",
        "areas",
        "reminder_settings",
        "import_subscribers",
        "import_bookings",
        "invite_team",
        "auto_top_up",
    ]
    assert not any(i["done"] for i in data["checklist"]["items"])

    with tenant_context(client_tenant):
        Area.objects.create(tenant=client_tenant, name="Edappally")
    other = factories.make_user("manager2@agency.test")
    factories.add_membership(other, client_tenant, "manager", status=Membership.Status.INVITED)
    data = items(api.get("/api/portal/home").json())
    assert data["areas"]["done"] and data["invite_team"]["done"]
    assert not data["import_subscribers"]["done"]

    response = api.post(
        "/api/portal/checklist", {"item": "agency_details", "action": "done"}, format="json"
    )
    assert items({"checklist": response.json()})["agency_details"]["done"]
    response = api.post(
        "/api/portal/checklist", {"item": "auto_top_up", "action": "skip"}, format="json"
    )
    assert items({"checklist": response.json()})["auto_top_up"]["skipped"]
    with db_context(platform=True):
        assert (
            AuditLog.objects.filter(
                tenant=client_tenant, object_type="core.tenant", action="update"
            ).count()
            == 2
        )


def test_checklist_completes_on_the_seeded_agency(api, demo):
    api = as_role(api, demo, "owner")
    data = api.get("/api/portal/home").json()["checklist"]
    pending = {i["key"] for i in data["items"] if not i["done"]}
    assert pending == {"agency_details", "reminder_settings", "auto_top_up"}  # the rest are facts
    for item in ["agency_details", "reminder_settings"]:
        api.post("/api/portal/checklist", {"item": item, "action": "done"}, format="json")
    result = api.post(
        "/api/portal/checklist", {"item": "auto_top_up", "action": "skip"}, format="json"
    ).json()
    assert result["complete"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"item": "areas", "action": "done"},  # ticks itself
        {"item": "agency_details", "action": "skip"},  # only auto top-up can be skipped
        {"item": "auto_top_up", "action": "maybe"},
    ],
)
def test_checklist_rules(api, client_tenant, body):
    response = as_role(api, client_tenant, "owner").post(
        "/api/portal/checklist", body, format="json"
    )
    assert response.status_code == 400
