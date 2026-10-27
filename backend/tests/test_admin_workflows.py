"""Workflows 3.2 (suspend / activate) and 3.3 (support access), plus Users and Activity tabs."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts import services as account_services
from apps.accounts.models import Invite, Membership, TenantRole, User
from apps.audit.models import AuditLog
from apps.core.models import Tenant
from apps.core.signals import tenant_reactivated
from apps.core.tenancy import db_context, tenant_context
from apps.platform_admin import services
from apps.platform_admin.models import SupportSession
from tests import factories
from tests.conftest import sign_in
from tests.tenancy_app.models import Note

pytestmark = [pytest.mark.django_db, pytest.mark.urls("tests.urls_audit")]

URL = "/api/admin/tenants"
REASON = {"reason": "Unpaid invoice for October"}


@pytest.fixture
def admin_api(api, super_admin):
    sign_in(api, super_admin)
    return api


@pytest.fixture
def note(tenant):
    with tenant_context(tenant):
        return Note.objects.create(body="existing")


def audit(**filters):
    with db_context(platform=True):
        return list(AuditLog.objects.filter(**filters).order_by("created_at"))


# --- 3.2 suspend -------------------------------------------------------------------------------


def test_suspend_requires_a_reason(admin_api, tenant):
    for body in ({}, {"reason": ""}, {"reason": "bad"}, {"reason": "x" * 501}):
        response = admin_api.post(f"{URL}/{tenant.id}/suspend", body, format="json")
        assert response.status_code == 400
        assert "reason" in response.json()["fields"]
    tenant.refresh_from_db()
    assert tenant.status == "active"


def test_suspend_stops_sending_and_is_audited(admin_api, tenant):
    assert tenant.can_send()

    response = admin_api.post(f"{URL}/{tenant.id}/suspend", REASON, format="json")

    assert response.status_code == 200
    assert response.json()["status"] == "suspended"
    assert response.json()["suspension_reason"] == REASON["reason"]
    tenant.refresh_from_db()
    assert tenant.suspended_at is not None
    assert not tenant.can_send()
    [entry] = audit(tenant=tenant, action="status_change")
    assert (entry.before["status"], entry.after["status"]) == ("active", "suspended")
    assert entry.after["reason"] == REASON["reason"]


def test_suspending_twice_is_refused(admin_api, tenant):
    admin_api.post(f"{URL}/{tenant.id}/suspend", REASON, format="json")
    response = admin_api.post(f"{URL}/{tenant.id}/suspend", REASON, format="json")
    assert response.status_code == 409
    assert response.json()["code"] == "already_suspended"


def test_support_staff_cannot_suspend_or_activate(api, support_user, tenant):
    sign_in(api, support_user)
    assert api.post(f"{URL}/{tenant.id}/suspend", REASON, format="json").status_code == 403
    assert api.post(f"{URL}/{tenant.id}/activate", REASON, format="json").status_code == 403


def test_suspended_tenant_is_read_only_for_its_users(admin_api, tenant, manager, note):
    services.suspend_tenant(tenant, "Unpaid invoice")
    from rest_framework.test import APIClient

    client = APIClient()
    sign_in(client, manager)

    assert client.get("/t/notes").status_code == 200  # reads still work
    for response in (
        client.post("/t/notes", {"body": "new"}),
        client.patch(f"/t/notes/{note.pk}", {"body": "edit"}),
        client.delete(f"/t/notes/{note.pk}"),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "tenant_suspended"
    with tenant_context(tenant):
        assert list(Note.objects.values_list("body", flat=True)) == ["existing"]


# --- 3.2 activate ------------------------------------------------------------------------------


def test_activate_requires_a_reason_and_restores_sending(admin_api, tenant):
    services.suspend_tenant(tenant, "Unpaid invoice")

    assert admin_api.post(f"{URL}/{tenant.id}/activate", {}, format="json").status_code == 400
    response = admin_api.post(
        f"{URL}/{tenant.id}/activate", {"reason": "Invoice paid"}, format="json"
    )

    assert response.status_code == 200
    tenant.refresh_from_db()
    assert (tenant.status, tenant.suspended_at, tenant.suspension_reason) == ("active", None, "")
    assert tenant.can_send()
    entry = audit(tenant=tenant, action="status_change")[-1]
    assert (entry.before["status"], entry.after["status"], entry.after["reason"]) == (
        "suspended",
        "active",
        "Invoice paid",
    )


def test_activating_an_active_tenant_is_refused(admin_api, tenant):
    response = admin_api.post(f"{URL}/{tenant.id}/activate", REASON, format="json")
    assert response.json()["code"] == "already_active"


def test_reactivation_calls_the_reminder_engine_hook(admin_api, tenant):
    services.suspend_tenant(tenant, "Unpaid invoice")
    suspended_at = Tenant.objects.get(pk=tenant.pk).suspended_at
    calls = []

    def receiver(sender, tenant, suspended_at, **kwargs):
        calls.append((tenant.pk, suspended_at))

    tenant_reactivated.connect(receiver)
    try:
        admin_api.post(f"{URL}/{tenant.id}/activate", {"reason": "Invoice paid"}, format="json")
    finally:
        tenant_reactivated.disconnect(receiver)

    assert calls == [(tenant.pk, suspended_at)]


def test_writes_work_again_after_activation(tenant, manager):
    services.suspend_tenant(tenant, "Unpaid invoice")
    services.activate_tenant(tenant, "Invoice paid")
    from rest_framework.test import APIClient

    client = APIClient()
    sign_in(client, manager)
    assert client.post("/t/notes", {"body": "new"}).status_code == 201


# --- Users and Activity tabs -------------------------------------------------------------------


def test_users_tab_lists_memberships_with_2fa_state(admin_api, tenant, owner, manager):
    factories.add_totp_device(owner)

    rows = admin_api.get(f"{URL}/{tenant.id}/users").json()

    assert [(r["email"], r["role"], r["status"], r["otp_enabled"]) for r in rows] == [
        ("owner@example.com", "owner", "active", True),
        ("manager@example.com", "manager", "active", False),
    ]


def test_users_tab_never_shows_another_tenants_members(admin_api, tenant, owner):
    other = factories.make_tenant("Other")
    factories.add_membership(factories.make_user("stranger@example.com"), other, TenantRole.OWNER)

    assert [r["email"] for r in admin_api.get(f"{URL}/{tenant.id}/users").json()] == [
        "owner@example.com"
    ]
    assert [r["email"] for r in admin_api.get(f"{URL}/{other.id}/users").json()] == [
        "stranger@example.com"
    ]


def test_tenant_context_turns_platform_access_off(tenant, owner):
    other = factories.make_tenant("Other")
    factories.add_membership(factories.make_user("stranger@example.com"), other, TenantRole.OWNER)
    with db_context(platform=True):
        assert Membership.objects.count() == 2
        with tenant_context(tenant):
            assert list(Membership.objects.values_list("user__email", flat=True)) == [
                "owner@example.com"
            ]


def test_resend_invite_issues_a_new_link_and_is_audited(
    admin_api, tenant, django_capture_on_commit_callbacks
):
    user = User.objects.create_user("invitee@example.com")
    membership = factories.add_membership(
        user, tenant, TenantRole.MANAGER, status=Membership.Status.INVITED
    )
    old_token = account_services.create_invite(membership)

    with django_capture_on_commit_callbacks(execute=True):
        response = admin_api.post(f"{URL}/{tenant.id}/users/{membership.pk}/resend-invite")

    assert response.status_code == 204
    assert admin_api.get(f"/api/auth/invites/{old_token}").json()["code"] == "invite_invalid"
    assert len(mail.outbox) == 1
    with tenant_context(tenant):
        assert Invite.objects.filter(membership=membership, revoked_at=None).count() == 1
    assert len(audit(tenant=tenant, action="create", object_type="accounts.invite")) == 2


def test_resend_invite_is_refused_for_active_members(admin_api, tenant, manager):
    with tenant_context(tenant):
        membership = Membership.objects.get(user=manager)
    response = admin_api.post(f"{URL}/{tenant.id}/users/{membership.pk}/resend-invite")
    assert response.status_code == 409


def test_resend_invite_cannot_reach_another_tenants_member(admin_api, tenant, manager):
    other = factories.make_tenant("Other")
    with tenant_context(tenant):
        membership = Membership.objects.get(user=manager)
    response = admin_api.post(f"{URL}/{other.id}/users/{membership.pk}/resend-invite")
    assert response.status_code == 404


def test_activity_tab_shows_only_this_tenants_entries(admin_api, tenant):
    other = factories.make_tenant("Other")
    services.suspend_tenant(tenant, "Unpaid invoice")
    services.suspend_tenant(other, "Unpaid invoice")

    page = admin_api.get(f"{URL}/{tenant.id}/activity").json()

    assert page["count"] == 1
    assert page["results"][0]["action"] == "status_change"
    assert page["results"][0]["tenant_id"] == str(tenant.id)


# --- 3.3 support access ------------------------------------------------------------------------


def start_support(api, tenant, reason="Owner cannot see bookings"):
    return api.post(f"{URL}/{tenant.id}/support-sessions", {"reason": reason}, format="json")


@pytest.mark.parametrize("role", ["super_admin", "support_user"])
def test_platform_staff_open_a_client_as_support_for_60_minutes(request, api, role, tenant):
    sign_in(api, request.getfixturevalue(role))

    response = start_support(api, tenant)

    assert response.status_code == 201
    data = response.json()
    assert data["tenant_name"] == "Kochi Gas Agency"
    assert 3590 <= data["seconds_left"] <= 3600
    current = api.get("/api/admin/support-sessions/current").json()
    assert current["id"] == data["id"]
    [entry] = audit(action="support_start")
    assert str(entry.support_session_id) == data["id"]
    assert entry.after["reason"] == "Owner cannot see bookings"


def test_support_session_needs_a_reason(admin_api, tenant):
    assert start_support(admin_api, tenant, reason="").status_code == 400
    assert not SupportSession.objects.exists()


def test_tenant_users_cannot_open_support_sessions(api, owner, tenant):
    sign_in(api, owner)
    assert start_support(api, tenant).status_code == 403


def test_support_mode_shows_the_tenant_and_tags_audit_entries(admin_api, tenant, note):
    session_id = start_support(admin_api, tenant).json()["id"]

    rows = admin_api.get("/t/notes").json()["results"]

    assert [r["body"] for r in rows] == ["existing"]
    other = factories.make_tenant("Other")
    # An admin action taken through the API during the support session is tagged with it.
    admin_api.post(f"{URL}/{other.id}/suspend", {"reason": "Checking audit tagging"}, format="json")
    [entry] = audit(tenant=other, action="status_change")
    assert str(entry.support_session_id) == session_id


def test_support_mode_is_read_only(admin_api, tenant, note):
    start_support(admin_api, tenant)

    response = admin_api.post("/t/notes", {"body": "new"})

    assert response.status_code == 403
    assert response.json()["code"] == "support_read_only"


def test_ending_support_returns_to_no_tenant_and_is_audited(admin_api, tenant, note):
    session_id = start_support(admin_api, tenant).json()["id"]

    response = admin_api.post(f"/api/admin/support-sessions/{session_id}/end")

    assert response.status_code == 200
    assert response.json()["end_reason"] == "manual"
    assert admin_api.get("/api/admin/support-sessions/current").status_code == 204
    assert admin_api.get("/t/notes").status_code == 403  # no tenant any more
    assert [e.action for e in audit(support_session_id=session_id)] == [
        "support_start",
        "support_end",
    ]
    assert admin_api.post(f"/api/admin/support-sessions/{session_id}/end").status_code == 409


def test_expired_session_is_refused_with_403(admin_api, tenant, note):
    session_id = start_support(admin_api, tenant).json()["id"]
    with tenant_context(tenant):
        SupportSession.objects.filter(pk=session_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

    response = admin_api.get("/t/notes")

    assert response.status_code == 403
    assert response.json()["code"] == "support_session_expired"
    assert admin_api.get("/api/admin/support-sessions/current").status_code == 204


def test_expiry_task_ends_overdue_sessions_and_audits_them(super_admin, tenant):
    session = services.start_support_session(super_admin, tenant, "Owner cannot see bookings")
    fresh = services.start_support_session(
        factories.make_user(platform_role="support"), tenant, "Second look at bookings"
    )
    with tenant_context(tenant):
        SupportSession.objects.filter(pk=session.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

    from apps.platform_admin.tasks import expire_support_sessions_task

    assert expire_support_sessions_task.delay().get() == 1

    with tenant_context(tenant):
        session.refresh_from_db()
        fresh.refresh_from_db()
    assert session.end_reason == "expired" and session.ended_at is not None
    assert fresh.ended_at is None
    [entry] = audit(action="support_end")
    assert entry.actor_id is None  # ended by the system
    assert entry.support_session_id == session.pk


def test_beat_runs_the_expiry_task_every_minute(settings):
    job = settings.CELERY_BEAT_SCHEDULE["expire-support-sessions"]
    assert job == {"task": "apps.platform_admin.tasks.expire_support_sessions", "schedule": 60.0}


def test_opening_a_second_session_ends_the_first(admin_api, tenant):
    other = factories.make_tenant("Other")
    first = start_support(admin_api, tenant).json()["id"]
    second = start_support(admin_api, other).json()["id"]

    with db_context(platform=True):
        assert SupportSession.objects.get(pk=first).end_reason == "replaced"
        assert SupportSession.objects.get(pk=second).ended_at is None
    assert admin_api.get("/api/admin/support-sessions/current").json()["tenant_name"] == "Other"
