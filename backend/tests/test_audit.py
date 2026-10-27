"""Audit log (Step 4): write_audit(), the DRF mixin, insert-only rules and tenant visibility."""

import pytest
from django.db import ProgrammingError, connection, transaction
from django.http import HttpResponse
from django.test import RequestFactory

from apps.accounts import services as account_services
from apps.accounts.models import Membership, TenantRole, User
from apps.audit.context import AuditContextMiddleware
from apps.audit.models import Action, AuditLog, AuditLogImmutableError
from apps.audit.services import MASK, snapshot, write_audit
from apps.core.middleware import TenantMiddleware
from apps.core.tenancy import db_context, has_platform_access, tenant_context
from tests import factories
from tests.conftest import sign_in
from tests.tenancy_app.models import Note

pytestmark = pytest.mark.django_db


def entries(**filters):
    """All audit entries, read with platform access (sees every tenant)."""
    with db_context(platform=True):
        return list(AuditLog.objects.filter(**filters).order_by("created_at"))


# --- write_audit() and snapshot() --------------------------------------------------------------


def test_write_audit_records_actor_ip_user_agent_and_json(tenant, manager):
    request = RequestFactory().get("/", REMOTE_ADDR="10.1.2.3", HTTP_USER_AGENT="pytest-agent")
    request.user = manager

    def view(req):
        with tenant_context(tenant):
            note = Note.objects.create(body="hello")
            write_audit(Action.CREATE, note, after=snapshot(note))
        return note

    note = AuditContextMiddleware(view)(request)

    [entry] = entries(object_id=str(note.pk))
    assert entry.action == "create"
    assert entry.object_type == "tenancy_app.note"
    assert entry.tenant_id == tenant.id
    assert entry.actor_id == manager.id
    assert entry.actor_email == "manager@example.com"
    assert entry.ip_address == "10.1.2.3"
    assert entry.user_agent == "pytest-agent"
    assert entry.after["body"] == "hello"
    assert entry.after["tenant_id"] == str(tenant.id)  # UUIDs and datetimes stored as JSON
    assert isinstance(entry.after["created_at"], str)


def test_system_actions_have_no_actor(tenant):
    entry = write_audit("nightly_job", tenant=tenant)
    assert entry.actor_id is None
    assert entry.actor_email == ""


def test_x_forwarded_for_is_used_only_when_trusted(settings, manager):
    factory = RequestFactory()
    request = factory.get("/", REMOTE_ADDR="10.0.0.9", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")
    request.user = manager
    ips = []

    def view(req):
        ips.append(write_audit("probe", tenant=None).ip_address)

    AuditContextMiddleware(view)(request)
    settings.AUDIT_TRUST_X_FORWARDED_FOR = True
    AuditContextMiddleware(view)(request)

    assert ips == ["10.0.0.9", "203.0.113.7"]


def test_support_session_is_recorded_when_present(tenant, support_user):
    from apps.platform_admin.services import start_support_session

    session = start_support_session(support_user, tenant, "Customer asked for help")
    request = RequestFactory().get("/")
    request.user = support_user
    request.support_session_id = session.pk

    AuditContextMiddleware(lambda req: write_audit("probe", tenant=tenant))(request)

    [entry] = entries(action="probe")
    assert entry.support_session_id == session.pk


def test_snapshot_masks_secrets(manager):
    data = snapshot(manager)
    assert data["password"] == MASK
    assert data["email"] == "manager@example.com"


# --- DRF mixin ---------------------------------------------------------------------------------


@pytest.mark.urls("tests.urls_audit")
class TestAuditedModelMixin:
    def test_create_update_status_change_and_delete_are_audited(self, api, tenant, manager):
        sign_in(api, manager)

        note_id = api.post("/t/notes", {"body": "first"}).json()["id"]
        api.patch(f"/t/notes/{note_id}", {"body": "edited"})
        api.patch(f"/t/notes/{note_id}", {"status": "closed"})
        assert api.delete(f"/t/notes/{note_id}").status_code == 204

        log = entries(object_id=note_id)
        assert [e.action for e in log] == ["create", "update", "status_change", "delete"]
        create, update, status_change, delete = log
        assert create.before is None and create.after["body"] == "first"
        assert (update.before["body"], update.after["body"]) == ("first", "edited")
        assert (status_change.before["status"], status_change.after["status"]) == ("open", "closed")
        assert delete.before["body"] == "edited" and delete.after is None
        assert {e.tenant_id for e in log} == {tenant.id}
        assert {e.actor_id for e in log} == {manager.id}

    def test_failed_write_leaves_no_audit_entry(self, api, manager):
        sign_in(api, manager)
        response = api.post("/t/notes", {"body": "x" * 101})

        assert response.status_code == 400
        assert entries(object_type="tenancy_app.note") == []


# --- insert-only: Django level -----------------------------------------------------------------


def test_saving_an_existing_entry_is_blocked(tenant):
    entry = write_audit("probe", tenant=tenant)
    entry.action = "tampered"
    with pytest.raises(AuditLogImmutableError):
        entry.save()


def test_deleting_an_entry_is_blocked(tenant):
    entry = write_audit("probe", tenant=tenant)
    with pytest.raises(AuditLogImmutableError):
        entry.delete()


def test_queryset_update_and_delete_are_blocked(tenant):
    write_audit("probe", tenant=tenant)
    with db_context(platform=True):
        with pytest.raises(AuditLogImmutableError):
            AuditLog.objects.filter(action="probe").update(action="tampered")
        with pytest.raises(AuditLogImmutableError):
            AuditLog.objects.filter(action="probe").delete()
        with pytest.raises(AuditLogImmutableError):
            AuditLog.objects.bulk_update([], ["action"])


# --- insert-only: database level ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_auditlog SET action = 'tampered'",
        "UPDATE audit_auditlog SET after = '{}'::jsonb",
        "DELETE FROM audit_auditlog",
    ],
)
def test_database_trigger_blocks_raw_update_and_delete(tenant, sql):
    write_audit("probe", tenant=tenant)
    with (
        pytest.raises(ProgrammingError, match="insert-only"),
        db_context(platform=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)
    assert [e.action for e in entries(action="probe")] == ["probe"]


def test_trigger_and_rls_are_installed():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'audit_auditlog'::regclass "
            "AND NOT tgisinternal"
        )
        assert cursor.fetchall() == [("audit_auditlog_insert_only",)]
        cursor.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'audit_auditlog'::regclass"
        )
        assert cursor.fetchone() == (True, True)


# --- tenant visibility (RLS) -------------------------------------------------------------------


def test_tenants_see_only_their_own_entries_and_platform_staff_see_all(tenant):
    other = factories.make_tenant("Other Agency")
    write_audit("a_event", tenant=tenant)
    write_audit("b_event", tenant=other)
    write_audit("platform_event", tenant=None)

    with tenant_context(tenant):
        assert set(AuditLog.objects.values_list("action", flat=True)) == {"a_event"}
    with tenant_context(other):
        assert set(AuditLog.objects.values_list("action", flat=True)) == {"b_event"}
    assert AuditLog.objects.count() == 0  # no tenant, no platform access
    with db_context(platform=True):
        assert set(AuditLog.objects.values_list("action", flat=True)) == {
            "a_event",
            "b_event",
            "platform_event",
        }


def test_entry_cannot_be_written_into_another_tenant(tenant):
    other = factories.make_tenant("Other Agency")
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        tenant_context(tenant),
        transaction.atomic(),
    ):
        AuditLog.objects.create(tenant=other, action="forged")


@pytest.mark.parametrize(
    ("fixture", "verified", "expected"),
    [
        ("super_admin", True, True),
        ("support_user", True, True),
        ("super_admin", False, False),
        ("owner", True, False),
    ],
)
def test_middleware_grants_platform_access_only_to_verified_staff(
    request, fixture, verified, expected
):
    user = request.getfixturevalue(fixture)
    user.is_verified = lambda: verified
    http_request = RequestFactory().get("/")
    http_request.user, http_request.session = user, {}

    seen = []
    TenantMiddleware(lambda req: seen.append(has_platform_access()) or HttpResponse())(http_request)
    assert seen == [expected]


# --- Step 3 actions are audited ----------------------------------------------------------------


def test_login_logout_and_2fa_are_audited(api, super_admin):
    sign_in(api, super_admin)  # password + 2FA code
    api.post("/api/auth/logout")

    actions = [e.action for e in entries(object_id=str(super_admin.pk))]
    assert actions == ["login", "otp_verified", "logout"]
    assert all(e.tenant_id is None for e in entries(object_id=str(super_admin.pk)))


def test_failed_logins_and_lock_out_are_audited(api, office_staff):
    for _ in range(5):
        api.post("/api/auth/login", {"email": office_staff.email, "password": "wrong-password!!"})
    api.post("/api/auth/login", {"email": "ghost@example.com", "password": "wrong-password!!"})

    log = entries(action__in=["login_failed", "account_locked"])
    assert [e.action for e in log] == ["login_failed"] * 5 + ["account_locked", "login_failed"]
    assert log[0].after == {"email": office_staff.email, "reason": "wrong_password"}
    assert log[-1].after == {"email": "ghost@example.com", "reason": "unknown_email"}
    assert log[-1].object_id == ""


def test_invite_create_and_accept_are_audited(api, tenant):
    user = User.objects.create_user("invitee@example.com")
    membership = factories.add_membership(
        user, tenant, TenantRole.MANAGER, status=Membership.Status.INVITED
    )
    token = account_services.create_invite(membership)
    api.post(f"/api/auth/invites/{token}/accept", {"password": "a-brand-new-passphrase"})

    created = entries(action="create", object_type="accounts.invite")
    assert len(created) == 1
    assert created[0].after["token_hash"] == MASK
    assert created[0].tenant_id == tenant.id
    accepted = entries(action="invite_accepted")
    assert accepted[0].actor_id == user.id
    [status_change] = entries(action="status_change", object_type="accounts.membership")
    assert (status_change.before["status"], status_change.after["status"]) == ("invited", "active")
