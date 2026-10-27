"""Login lock-out and invite rules from docs/admin-console-spec.md (2.1 and Workflow 3.1)."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.exceptions import ApiError
from apps.core.tenancy import tenant_context

from .models import Invite, Membership, User

MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)
INVITE_LIFETIME = timedelta(hours=72)

INVALID_CREDENTIALS = ApiError("invalid_credentials", "Invalid email or password.", 400)


# --- login -------------------------------------------------------------------------------------


def authenticate_with_lockout(email: str, password: str) -> User:
    """
    Check email + password. Five consecutive failures lock the account for 15 minutes. Attempts
    during a lock are refused with the same message and not counted. Never reveals whether the
    email exists.
    """
    now = timezone.now()
    authenticated, reason, locked_now = None, None, False
    # The failure is raised after the block, so the counter update is not rolled back with it.
    with transaction.atomic():
        user = User.objects.select_for_update().filter(email__iexact=email.strip()).first()
        if user is None:
            # Spend the same hashing time as a real check so timing doesn't reveal unknown emails.
            User().set_password(password)
            reason = "unknown_email"
        elif user.is_locked(now):
            reason = "locked"
        elif not user.check_password(password) or not user.is_active:
            reason = "wrong_password" if user.is_active else "inactive"
            user.failed_login_count += 1
            update = ["failed_login_count"]
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.failed_login_count = 0
                user.locked_until = now + LOCK_DURATION
                update.append("locked_until")
                locked_now = True
            user.save(update_fields=update)
        else:
            if user.failed_login_count or user.locked_until:
                user.failed_login_count = 0
                user.locked_until = None
                user.save(update_fields=["failed_login_count", "locked_until"])
            authenticated = user
    if authenticated is None:
        audit_target = {"obj": user} if user else {"object_type": "accounts.user"}
        write_audit(
            Action.LOGIN_FAILED,
            **audit_target,
            after={"email": email.strip().lower(), "reason": reason},
            tenant=None,
            actor=None,
        )
        if locked_now:
            write_audit(
                Action.ACCOUNT_LOCKED,
                user,
                after={"locked_until": user.locked_until},
                tenant=None,
                actor=None,
            )
        raise INVALID_CREDENTIALS
    return authenticated


# --- invites -----------------------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invite_url(token: str) -> str:
    return f"{settings.PORTAL_BASE_URL.rstrip('/')}/invite/{token}"


def create_invite(membership: Membership, created_by: User | None = None) -> str:
    """
    Create an invite for `membership` and return the raw token (shown once, emailed). Any earlier
    unused invite for the membership is revoked, so resending invalidates old links. The token is
    "<tenant id>.<secret>": the tenant part lets acceptance set the tenant context; only the hash
    of the whole token is stored. The email is sent after the transaction commits.
    """
    from .tasks import send_invite_email

    now = timezone.now()
    token = f"{membership.tenant_id}.{secrets.token_urlsafe(32)}"
    with tenant_context(membership.tenant_id):
        revoked = Invite.objects.filter(
            membership=membership, used_at=None, revoked_at=None
        ).update(revoked_at=now)
        invite = Invite.objects.create(
            tenant_id=membership.tenant_id,
            membership=membership,
            token_hash=hash_token(token),
            expires_at=now + INVITE_LIFETIME,
            created_by=created_by,
        )
        write_audit(
            Action.CREATE, invite, after={**snapshot(invite), "revoked_earlier_invites": revoked}
        )
        transaction.on_commit(
            lambda: send_invite_email.delay(
                tenant_id=str(membership.tenant_id), invite_id=str(invite.pk), token=token
            )
        )
    return token


def resend_invite(membership: Membership, created_by: User | None = None) -> str:
    if membership.status != Membership.Status.INVITED:
        raise ApiError("not_invited", "This user has already accepted their invite.", 409)
    return create_invite(membership, created_by)


def _tenant_from_token(token: str) -> uuid.UUID:
    tenant_part, _, secret = token.partition(".")
    try:
        tenant_id = uuid.UUID(tenant_part)
    except ValueError:
        raise ApiError("invite_invalid", "This invite link is not valid.", 404) from None
    if not secret:
        raise ApiError("invite_invalid", "This invite link is not valid.", 404)
    return tenant_id


def get_valid_invite(token: str) -> Invite:
    """Return the invite for `token`, or raise invite_invalid / invite_used / invite_expired."""
    tenant_id = _tenant_from_token(token)
    with tenant_context(tenant_id):
        invite = (
            Invite.objects.select_related("membership__user", "membership__tenant")
            .filter(token_hash=hash_token(token))
            .first()
        )
    if invite is None or invite.revoked_at is not None:
        raise ApiError("invite_invalid", "This invite link is not valid.", 404)
    if invite.used_at is not None:
        raise ApiError("invite_used", "This invite has already been used.", 410)
    if invite.expires_at <= timezone.now():
        raise ApiError("invite_expired", "This invite has expired. Ask for a new one.", 410)
    return invite


def accept_invite(token: str, password: str, full_name: str = "") -> User:
    """
    Accept an invite. A new user sets a password (validated: 12+ characters etc.); an existing user
    with a password confirms it. The invite becomes used and the membership active.
    """
    invite = get_valid_invite(token)
    user = invite.membership.user
    with tenant_context(invite.tenant_id):
        invite = Invite.objects.select_for_update().get(pk=invite.pk)
        if invite.used_at is not None:
            raise ApiError("invite_used", "This invite has already been used.", 410)
        if user.has_usable_password():
            if not user.check_password(password):
                raise ApiError("invalid_credentials", "That password is not correct.", 400)
        else:
            try:
                password_validation.validate_password(password, user)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"password": exc.messages}) from exc
            user.set_password(password)
        if full_name:
            user.full_name = full_name
        user.save()
        invite_before = snapshot(invite)
        invite.used_at = timezone.now()
        invite.save(update_fields=["used_at", "updated_at"])
        write_audit(
            Action.INVITE_ACCEPTED, invite, before=invite_before, after=snapshot(invite), actor=user
        )
        membership = Membership.objects.get(pk=invite.membership_id)
        membership_before = snapshot(membership)
        membership.status = Membership.Status.ACTIVE
        membership.save(update_fields=["status", "updated_at"])
        write_audit(
            Action.STATUS_CHANGE,
            membership,
            before=membership_before,
            after=snapshot(membership),
            actor=user,
        )
    return user
