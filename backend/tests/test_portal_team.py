"""Portal Users and Reminder settings (spec 2.10), agency details, issue_invite_link."""

import datetime as dt
import io

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.accounts.models import Invite, Membership, User
from apps.audit.models import AuditLog
from apps.core.tenancy import db_context, tenant_context
from apps.industry.services import tenant_config
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db
USERS = "/api/portal/users"
SETTINGS = "/api/portal/reminder-settings"
TODAY = timezone.localdate()


@pytest.fixture
def owner_api(portal):
    return portal("owner")


def members(tenant):
    with tenant_context(tenant):
        return {
            m.user.email: m for m in Membership.objects.filter(tenant=tenant).select_related("user")
        }


# --- users ----------------------------------------------------------------------------------


def test_owner_invites_a_new_person(owner_api, client_tenant, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = owner_api.post(
            USERS,
            {"email": " Rahul@Agency.Test ", "full_name": "Rahul", "role": "manager"},
            format="json",
        )
    assert response.status_code == 201, response.json()
    data = response.json()
    assert (data["email"], data["role"], data["status"]) == (
        "rahul@agency.test",
        "manager",
        "invited",
    )
    assert members(client_tenant)["rahul@agency.test"].status == "invited"
    assert len(mail.outbox) == 1 and "/invite/" in mail.outbox[0].body
    with db_context(platform=True):
        assert AuditLog.objects.filter(object_type="accounts.invite", tenant=client_tenant).exists()


def test_existing_user_gets_a_membership(owner_api, client_tenant):
    other = factories.make_tenant("Other Agency")
    person = factories.make_user("shared@agency.test")
    factories.add_membership(person, other, "manager")
    response = owner_api.post(
        USERS,
        {"email": "shared@agency.test", "full_name": "Shared", "role": "office_staff"},
        format="json",
    )
    assert response.status_code == 201
    assert User.objects.filter(email="shared@agency.test").count() == 1


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"email": "not-an-email", "full_name": "X Y", "role": "manager"}, "email"),
        ({"email": "a@b.test", "full_name": "X", "role": "manager"}, "full_name"),
        ({"email": "a@b.test", "full_name": "X Y", "role": "super_admin"}, "role"),
    ],
)
def test_invite_rules(owner_api, body, field):
    response = owner_api.post(USERS, body, format="json")
    assert response.status_code == 400 and field in response.json()["fields"]


def test_cannot_invite_a_member_twice_or_platform_staff(owner_api, client_tenant, super_admin):
    body = {"email": "rahul@agency.test", "full_name": "Rahul", "role": "manager"}
    assert owner_api.post(USERS, body, format="json").status_code == 201
    assert owner_api.post(USERS, body, format="json").json()["code"] == "already_member"
    staff = {"email": super_admin.email, "full_name": "Staff", "role": "manager"}
    assert owner_api.post(USERS, staff, format="json").json()["code"] == "staff_account"


def test_change_role_and_last_owner_guard(owner_api, client_tenant):
    me = next(m for m in members(client_tenant).values() if m.role == "owner")
    response = owner_api.post(f"{USERS}/{me.pk}/role", {"role": "manager"}, format="json")
    assert response.status_code == 409 and response.json()["code"] == "last_owner"
    response = owner_api.post(f"{USERS}/{me.pk}/deactivate")
    assert response.json()["code"] == "last_owner"

    second = factories.make_user("second-owner@agency.test")
    factories.add_membership(second, client_tenant, "owner")
    assert (
        owner_api.post(f"{USERS}/{me.pk}/role", {"role": "manager"}, format="json").json()["role"]
        == "manager"
    )
    with db_context(platform=True):
        entry = AuditLog.objects.filter(object_id=str(me.pk), action="update").latest("created_at")
    assert entry.before["role"] == "owner" and entry.after["role"] == "manager"


def test_deactivate_and_resend(owner_api, client_tenant, django_capture_on_commit_callbacks):
    staff = factories.make_user("staff1@agency.test")
    active = factories.add_membership(staff, client_tenant, "office_staff")
    response = owner_api.post(f"{USERS}/{active.pk}/deactivate")
    assert response.json()["status"] == "deactivated"
    assert owner_api.post(f"{USERS}/{active.pk}/resend-invite").json()["code"] == "not_invited"

    invited = owner_api.post(
        USERS,
        {"email": "new@agency.test", "full_name": "New Person", "role": "manager"},
        format="json",
    ).json()
    with django_capture_on_commit_callbacks(execute=True):
        assert owner_api.post(f"{USERS}/{invited['id']}/resend-invite").status_code == 200
    with tenant_context(client_tenant):
        invites = Invite.objects.filter(membership_id=invited["id"])
        assert invites.count() == 2 and invites.filter(revoked_at=None).count() == 1


def test_list_marks_the_caller(owner_api, client_tenant):
    rows = owner_api.get(USERS).json()
    assert [r["is_you"] for r in rows].count(True) == 1
    assert {"email", "role", "status", "last_login", "otp_enabled"} <= set(rows[0])


@pytest.mark.parametrize("role", ["manager", "office_staff"])
def test_users_screen_is_owner_only(portal, role):
    api = portal(role)
    assert api.get(USERS).status_code == 403
    assert (
        api.post(
            USERS, {"email": "x@y.test", "full_name": "X Y", "role": "manager"}, format="json"
        ).status_code
        == 403
    )


def test_other_agencies_members_are_not_reachable(owner_api):
    other = factories.make_tenant("Elsewhere")
    theirs = factories.add_membership(factories.make_user("their@agency.test"), other, "manager")
    assert owner_api.post(f"{USERS}/{theirs.pk}/deactivate").status_code == 404
    assert "their@agency.test" not in [r["email"] for r in owner_api.get(USERS).json()]


# --- reminder settings ----------------------------------------------------------------------


def settings_body(**overrides):
    body = {
        "send_time": "09:30",
        "days_before": 2,
        "wait_minutes": 180,
        "quiet_start": "21:00",
        "quiet_end": "08:00",
        "default_language": "en",
        "delivery_weekdays": [0, 1, 2, 3, 4],
        "holiday_dates": [],
    }
    body.update(overrides)
    return body


def test_defaults_come_from_the_industry_template(portal):
    data = portal("manager").get(SETTINGS).json()
    assert (data["send_time"], data["days_before"], data["wait_minutes"]) == ("10:00", 1, 240)
    assert (data["quiet_start"], data["quiet_end"]) == ("21:00", "08:00")
    assert data["delivery_weekdays"] == [0, 1, 2, 3, 4, 5]
    assert data["default_language"] == "ml" and data["saved_at"] is None
    assert data["schedule_type"] == "cylinder_delivery"


def test_saving_updates_the_rule_language_and_checklist(owner_api, client_tenant):
    holiday = (TODAY + dt.timedelta(days=10)).isoformat()
    response = owner_api.put(
        SETTINGS, settings_body(holiday_dates=[holiday, holiday]), format="json"
    )
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["holiday_dates"] == [holiday] and data["saved_at"]
    client_tenant.refresh_from_db()
    rule = tenant_config(client_tenant)["schedule_types"][0]["reminder_rule"]
    assert (rule["send_time"], rule["days_before"], rule["delivery_weekdays"]) == (
        "09:30",
        2,
        [0, 1, 2, 3, 4],
    )
    assert rule["channel_order"] == ["whatsapp"]  # other parts of the rule are kept
    assert client_tenant.default_language == "en"
    home = owner_api.get("/api/portal/home").json()
    assert next(i for i in home["checklist"]["items"] if i["key"] == "reminder_settings")["done"]
    with db_context(platform=True):
        entry = AuditLog.objects.filter(object_type="core.tenant", action="update").latest(
            "created_at"
        )
    assert entry.before["reminder_settings"]["send_time"] == "10:00"
    assert entry.after["reminder_settings"]["send_time"] == "09:30"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"send_time": "9:30"}, "send_time"),
        ({"send_time": "24:00"}, "send_time"),
        ({"send_time": "22:00"}, "send_time"),  # inside quiet hours (21:00-08:00)
        ({"send_time": "07:59"}, "send_time"),  # still inside, before the end
        ({"days_before": 8}, "days_before"),
        ({"days_before": -1}, "days_before"),
        ({"wait_minutes": 29}, "wait_minutes"),
        ({"wait_minutes": 1441}, "wait_minutes"),
        ({"quiet_end": "8am"}, "quiet_end"),
        ({"default_language": "fr"}, "default_language"),
        ({"delivery_weekdays": []}, "delivery_weekdays"),
        ({"delivery_weekdays": [0, 0]}, "delivery_weekdays"),
        ({"delivery_weekdays": [7]}, "delivery_weekdays"),
        ({"holiday_dates": [(TODAY - dt.timedelta(days=1)).isoformat()]}, "holiday_dates"),
    ],
)
def test_reminder_rules(owner_api, overrides, field):
    response = owner_api.put(SETTINGS, settings_body(**overrides), format="json")
    assert response.status_code == 400 and field in response.json()["fields"], response.json()


def test_quiet_hours_within_one_day(owner_api):
    body = settings_body(quiet_start="13:00", quiet_end="15:00", send_time="14:00")
    assert "send_time" in owner_api.put(SETTINGS, body, format="json").json()["fields"]
    assert (
        owner_api.put(
            SETTINGS,
            settings_body(quiet_start="13:00", quiet_end="15:00", send_time="10:00"),
            format="json",
        ).status_code
        == 200
    )


def test_office_staff_cannot_open_reminder_settings(portal):
    api = portal("office_staff")
    assert api.get(SETTINGS).status_code == 403
    assert api.put(SETTINGS, settings_body(), format="json").status_code == 403


def test_support_session_reads_settings_only(api, super_admin, client_tenant):
    from apps.platform_admin.services import start_support_session
    from apps.platform_admin.tenancy import SUPPORT_SESSION_KEY

    sign_in(api, super_admin)
    support = start_support_session(super_admin, client_tenant, "Checking reminder settings")
    session = api.session
    session[SUPPORT_SESSION_KEY] = str(support.pk)
    session.save()
    assert api.get(SETTINGS).status_code == 200
    assert api.get(USERS).status_code == 200  # support may view Users (spec 1.1)
    assert api.put(SETTINGS, settings_body(), format="json").status_code == 403


# --- agency and invite links ----------------------------------------------------------------


def test_agency_details_are_read_only(portal, client_tenant):
    api = portal("office_staff")
    data = api.get("/api/portal/agency").json()
    assert data["name"] == client_tenant.name and data["state"] == "KL"
    assert api.patch("/api/portal/agency", {"name": "Hacked"}, format="json").status_code == 405


def test_issue_invite_link(client_tenant, settings):
    person = factories.make_user("invitee@agency.test")
    factories.add_membership(person, client_tenant, "owner", status=Membership.Status.INVITED)
    with pytest.raises(CommandError, match="local development"):
        call_command("issue_invite_link", "invitee@agency.test", "--tenant", client_tenant.name)
    out = io.StringIO()
    call_command(
        "issue_invite_link",
        "invitee@agency.test",
        "--tenant",
        client_tenant.name,
        "--allow-non-debug",
        stdout=out,
    )
    assert f"/invite/{client_tenant.pk}." in out.getvalue()
    with pytest.raises(CommandError, match="not a member"):
        call_command(
            "issue_invite_link",
            "nobody@agency.test",
            "--tenant",
            client_tenant.name,
            "--allow-non-debug",
        )
