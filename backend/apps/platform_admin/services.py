"""Workflows 3.1 (create client), 3.2 (suspend / activate) and 3.3 (support access)."""

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Membership, TenantRole, User
from apps.accounts.services import create_invite
from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.signals import tenant_reactivated
from apps.core.tenancy import db_context, tenant_context
from apps.industry.services import assign_template

from .models import SUPPORT_SESSION_LENGTH, SupportSession

OWNER_FIELDS = ("owner_name", "owner_email", "owner_phone")


# --- 3.1 create client -------------------------------------------------------------------------


def create_client(data: dict, created_by: User) -> Tenant:
    """
    Create the tenant (with a copy of its industry template's defaults), the owner user (or reuse
    an existing one), an invited owner membership and the invite, in one transaction. The invite
    email is sent after commit.
    """
    tenant_fields = {k: v for k, v in data.items() if k not in OWNER_FIELDS}
    template = tenant_fields.pop("industry_template")
    owner_email = data["owner_email"].strip().lower()

    with transaction.atomic():
        tenant = Tenant(**tenant_fields)
        # Copy the template's defaults (reminder rule, question flow, choice lists) onto the client.
        assign_template(tenant, template)
        tenant.save()
        write_audit(Action.CREATE, tenant, after=snapshot(tenant))

        user = User.objects.filter(email__iexact=owner_email).first()
        if user is None:
            user = User.objects.create_user(
                owner_email, full_name=data["owner_name"], phone=data.get("owner_phone") or ""
            )
            write_audit(Action.CREATE, user, after=snapshot(user), tenant=tenant)

        with tenant_context(tenant):
            membership = Membership.objects.create(
                tenant=tenant, user=user, role=TenantRole.OWNER, status=Membership.Status.INVITED
            )
            write_audit(Action.CREATE, membership, after=snapshot(membership))
        create_invite(membership, created_by=created_by)
    return tenant


def update_tenant(tenant: Tenant, data: dict) -> Tenant:
    before = snapshot(tenant)
    for field, value in data.items():
        setattr(tenant, field, value)
    tenant.save()
    write_audit(Action.UPDATE, tenant, before=before, after=snapshot(tenant))
    return tenant


# --- 3.2 suspend / activate --------------------------------------------------------------------


def suspend_tenant(tenant: Tenant, reason: str) -> Tenant:
    if tenant.status == Tenant.Status.SUSPENDED:
        raise ApiError("already_suspended", "This client is already suspended.", 409)
    before = snapshot(tenant)
    tenant.status = Tenant.Status.SUSPENDED
    tenant.suspended_at = timezone.now()
    tenant.suspension_reason = reason
    tenant.save(update_fields=["status", "suspended_at", "suspension_reason", "updated_at"])
    write_audit(
        Action.STATUS_CHANGE, tenant, before=before, after={**snapshot(tenant), "reason": reason}
    )
    return tenant


def activate_tenant(tenant: Tenant, reason: str) -> Tenant:
    if tenant.status == Tenant.Status.ACTIVE:
        raise ApiError("already_active", "This client is already active.", 409)
    before = snapshot(tenant)
    suspended_at = tenant.suspended_at
    tenant.status = Tenant.Status.ACTIVE
    tenant.suspended_at = None
    tenant.suspension_reason = ""
    tenant.save(update_fields=["status", "suspended_at", "suspension_reason", "updated_at"])
    write_audit(
        Action.STATUS_CHANGE, tenant, before=before, after={**snapshot(tenant), "reason": reason}
    )
    on_tenant_reactivated(tenant, suspended_at)
    return tenant


def on_tenant_reactivated(tenant: Tenant, suspended_at):
    """Hook for the Phase 2 reminder engine: held reminders past their send time become Skipped."""
    tenant_reactivated.send(sender=Tenant, tenant=tenant, suspended_at=suspended_at)


# --- 3.3 support access ------------------------------------------------------------------------


def active_support_session(user: User) -> SupportSession | None:
    with db_context(platform=True):
        session = (
            SupportSession.objects.select_related("tenant")
            .filter(staff_user=user, ended_at=None, expires_at__gt=timezone.now())
            .first()
        )
    return session


def start_support_session(user: User, tenant: Tenant, reason: str) -> SupportSession:
    """Open `tenant` as support for 60 minutes. A user's earlier active session is ended first."""
    with transaction.atomic():
        previous = active_support_session(user)
        if previous is not None:
            end_support_session(previous, SupportSession.EndReason.REPLACED)
        now = timezone.now()
        with tenant_context(tenant):
            session = SupportSession.objects.create(
                tenant=tenant,
                staff_user=user,
                reason=reason,
                started_at=now,
                expires_at=now + SUPPORT_SESSION_LENGTH,
            )
            write_audit(
                Action.SUPPORT_START, session, after=snapshot(session), support_session=session
            )
    return session


def end_support_session(session: SupportSession, end_reason, actor=None) -> SupportSession:
    if session.ended_at is not None:
        return session
    with tenant_context(session.tenant_id):
        before = snapshot(session)
        session.ended_at = min(timezone.now(), session.expires_at)
        session.end_reason = end_reason
        session.save(update_fields=["ended_at", "end_reason", "updated_at"])
        audit_kwargs = {} if actor is None else {"actor": actor}
        if end_reason == SupportSession.EndReason.EXPIRED:
            audit_kwargs["actor"] = None  # ended by the system task
        write_audit(
            Action.SUPPORT_END,
            session,
            before=before,
            after=snapshot(session),
            support_session=session,
            **audit_kwargs,
        )
    return session


def expire_support_sessions() -> int:
    """End every session past its 60 minutes. Run every minute by Celery Beat."""
    with db_context(platform=True):
        overdue = list(SupportSession.objects.filter(ended_at=None, expires_at__lte=timezone.now()))
    for session in overdue:
        end_support_session(session, SupportSession.EndReason.EXPIRED)
    return len(overdue)
