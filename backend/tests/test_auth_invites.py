"""Invite flow (Workflow 3.1): hashed single-use token, 72-hour expiry, resend."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Invite, Membership, TenantRole, User
from apps.core.tenancy import tenant_context
from tests import factories

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "a-brand-new-passphrase"


@pytest.fixture
def invited(tenant):
    """A new owner who has been invited but has no password yet."""
    user = User.objects.create_user("new.owner@example.com")
    membership = factories.add_membership(
        user, tenant, TenantRole.OWNER, status=Membership.Status.INVITED
    )
    return membership


def invite_for(membership):
    with tenant_context(membership.tenant_id):
        return Invite.objects.filter(membership=membership).latest("created_at")


def test_invite_stores_only_the_token_hash(invited):
    token = services.create_invite(invited)
    invite = invite_for(invited)

    assert invite.token_hash == services.hash_token(token)
    assert token not in invite.token_hash
    assert token.startswith(f"{invited.tenant_id}.")


def test_invite_expires_after_72_hours(invited):
    services.create_invite(invited)
    lifetime = invite_for(invited).expires_at - timezone.now()

    assert timedelta(hours=71, minutes=59) < lifetime <= timedelta(hours=72)


def test_invite_email_is_sent_after_commit_with_the_link(
    invited, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        token = services.create_invite(invited)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["new.owner@example.com"]
    assert f"http://localhost:3001/invite/{token}" in mail.outbox[0].body


def test_invite_details_are_shown_for_a_valid_token(api, invited):
    token = services.create_invite(invited)

    data = api.get(f"/api/auth/invites/{token}").json()

    assert data["email"] == "new.owner@example.com"
    assert data["tenant_name"] == "Kochi Gas Agency"
    assert data["role"] == "owner"
    assert data["needs_new_password"] is True
    assert data["requires_2fa"] is True


def test_accepting_sets_the_password_activates_membership_and_signs_in(api, invited):
    token = services.create_invite(invited)

    response = api.post(
        f"/api/auth/invites/{token}/accept", {"password": NEW_PASSWORD, "full_name": "Asha Nair"}
    )

    assert response.status_code == 200
    state = response.json()
    assert state["otp_setup_required"] is True  # owners set up 2FA next
    user = User.objects.get(email="new.owner@example.com")
    assert user.check_password(NEW_PASSWORD)
    assert user.full_name == "Asha Nair"
    with tenant_context(invited.tenant_id):
        invited.refresh_from_db()
    assert invited.status == Membership.Status.ACTIVE
    assert invite_for(invited).used_at is not None


def test_invite_is_single_use(api, invited):
    token = services.create_invite(invited)
    api.post(f"/api/auth/invites/{token}/accept", {"password": NEW_PASSWORD})

    response = api.post(f"/api/auth/invites/{token}/accept", {"password": NEW_PASSWORD})

    assert response.status_code == 410
    assert response.json()["code"] == "invite_used"


def test_expired_invite_is_refused(api, invited):
    token = services.create_invite(invited)
    with tenant_context(invited.tenant_id):
        Invite.objects.filter(membership=invited).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

    for response in (
        api.get(f"/api/auth/invites/{token}"),
        api.post(f"/api/auth/invites/{token}/accept", {"password": NEW_PASSWORD}),
    ):
        assert response.status_code == 410
        assert response.json()["code"] == "invite_expired"
    assert not User.objects.get(email="new.owner@example.com").has_usable_password()


def test_resend_revokes_the_old_link_and_issues_a_new_72_hour_one(api, invited):
    old_token = services.create_invite(invited)
    new_token = services.resend_invite(invited)

    assert api.get(f"/api/auth/invites/{old_token}").json()["code"] == "invite_invalid"
    assert api.get(f"/api/auth/invites/{new_token}").status_code == 200
    with tenant_context(invited.tenant_id):
        live = Invite.objects.filter(membership=invited, revoked_at=None)
        assert live.count() == 1
        assert live.get().expires_at > timezone.now() + timedelta(hours=71)


def test_resend_is_refused_once_accepted(api, invited):
    token = services.create_invite(invited)
    api.post(f"/api/auth/invites/{token}/accept", {"password": NEW_PASSWORD})
    with tenant_context(invited.tenant_id):
        invited.refresh_from_db()

    with pytest.raises(services.ApiError, match="already accepted"):
        services.resend_invite(invited)


@pytest.mark.parametrize(
    "token_suffix",
    ["tampered", "", None],
    ids=["wrong-secret", "empty-secret", "not-a-tenant-id"],
)
def test_tampered_tokens_are_invalid(api, invited, token_suffix):
    services.create_invite(invited)
    token = "not-a-uuid.secret" if token_suffix is None else f"{invited.tenant_id}.{token_suffix}"

    response = api.get(f"/api/auth/invites/{token}")

    assert response.status_code == 404
    assert response.json()["code"] == "invite_invalid"


def test_token_cannot_be_replayed_against_another_tenant(api, invited):
    token = services.create_invite(invited)
    other = factories.make_tenant("Other Agency")
    swapped = f"{other.id}.{token.partition('.')[2]}"

    assert api.get(f"/api/auth/invites/{swapped}").json()["code"] == "invite_invalid"


def test_new_password_must_meet_the_rules(api, invited):
    token = services.create_invite(invited)

    response = api.post(f"/api/auth/invites/{token}/accept", {"password": "too-short"})

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert invite_for(invited).used_at is None


def test_existing_user_confirms_their_current_password(api, tenant):
    user = factories.make_user("existing@example.com")
    membership = factories.add_membership(
        user, tenant, TenantRole.MANAGER, status=Membership.Status.INVITED
    )
    token = services.create_invite(membership)

    wrong = api.post(f"/api/auth/invites/{token}/accept", {"password": NEW_PASSWORD})
    right = api.post(f"/api/auth/invites/{token}/accept", {"password": factories.PASSWORD})

    assert wrong.json()["code"] == "invalid_credentials"
    assert right.status_code == 200
    user.refresh_from_db()
    assert user.check_password(factories.PASSWORD)
