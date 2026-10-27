"""Login (spec 2.1): passwords, lock-out, CSRF and two-factor authentication."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db

LOGIN = "/api/auth/login"


def login(api, email, password):
    return api.post(LOGIN, {"email": email, "password": password})


# --- passwords and generic errors --------------------------------------------------------------


def test_login_with_correct_password_starts_a_session(api, office_staff):
    response = login(api, office_staff.email, factories.PASSWORD)

    assert response.status_code == 200
    assert response.json()["email"] == office_staff.email
    assert api.get("/api/auth/me").status_code == 200


def test_email_is_case_insensitive(api, office_staff):
    assert login(api, office_staff.email.upper(), factories.PASSWORD).status_code == 200


def test_wrong_password_and_unknown_email_get_the_same_answer(api, office_staff):
    wrong = login(api, office_staff.email, "not-the-password!")
    unknown = login(api, "nobody@example.com", "not-the-password!")

    assert wrong.status_code == unknown.status_code == 400
    assert (
        wrong.json()
        == unknown.json()
        == {
            "code": "invalid_credentials",
            "detail": "Invalid email or password.",
            "fields": {},
        }
    )


def test_inactive_user_cannot_log_in(api, office_staff):
    office_staff.is_active = False
    office_staff.save()

    assert login(api, office_staff.email, factories.PASSWORD).json()["code"] == (
        "invalid_credentials"
    )


def test_minimum_password_length_is_12(settings):
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="at least 12 characters"):
        validate_password("Short-pass1")
    validate_password("long-enough-passphrase")


# --- lock-out ----------------------------------------------------------------------------------


def test_five_failures_lock_the_account_for_15_minutes(api, office_staff):
    for _ in range(4):
        login(api, office_staff.email, "wrong-password!!")
    office_staff.refresh_from_db()
    assert office_staff.failed_login_count == 4
    assert office_staff.locked_until is None

    login(api, office_staff.email, "wrong-password!!")
    office_staff.refresh_from_db()
    assert office_staff.locked_until is not None
    remaining = office_staff.locked_until - timezone.now()
    assert timedelta(minutes=14) < remaining <= timedelta(minutes=15)


def test_correct_password_is_refused_while_locked(api, office_staff):
    for _ in range(5):
        login(api, office_staff.email, "wrong-password!!")

    response = login(api, office_staff.email, factories.PASSWORD)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_credentials"
    assert api.get("/api/auth/me").status_code == 403


def test_attempts_during_a_lock_are_not_counted(api, office_staff):
    for _ in range(5):
        login(api, office_staff.email, "wrong-password!!")
    office_staff.refresh_from_db()
    locked_until = office_staff.locked_until

    login(api, office_staff.email, "wrong-password!!")
    office_staff.refresh_from_db()
    assert office_staff.locked_until == locked_until
    assert office_staff.failed_login_count == 0


def test_login_works_again_after_the_lock_expires(api, office_staff):
    User.objects.filter(pk=office_staff.pk).update(
        locked_until=timezone.now() - timedelta(seconds=1), failed_login_count=0
    )

    assert login(api, office_staff.email, factories.PASSWORD).status_code == 200
    office_staff.refresh_from_db()
    assert office_staff.locked_until is None


def test_successful_login_resets_the_failure_count(api, office_staff):
    for _ in range(3):
        login(api, office_staff.email, "wrong-password!!")
    login(api, office_staff.email, factories.PASSWORD)
    office_staff.refresh_from_db()

    assert office_staff.failed_login_count == 0


# --- CSRF --------------------------------------------------------------------------------------


def test_login_requires_a_csrf_token(office_staff):
    client = APIClient(enforce_csrf_checks=True)
    response = client.post(LOGIN, {"email": office_staff.email, "password": factories.PASSWORD})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"

    client.get("/api/auth/csrf")
    token = client.cookies["csrftoken"].value
    response = client.post(
        LOGIN,
        {"email": office_staff.email, "password": factories.PASSWORD},
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200


def test_session_cookie_is_http_only(api, office_staff):
    login(api, office_staff.email, factories.PASSWORD)
    assert api.cookies["sessionid"]["httponly"]


# --- two-factor authentication -----------------------------------------------------------------


def test_super_admin_without_authenticator_must_set_one_up(api, super_admin):
    state = login(api, super_admin.email, factories.PASSWORD).json()

    assert state["requires_2fa"] is True
    assert state["otp_setup_required"] is True
    assert state["otp_verified"] is False


def test_super_admin_sets_up_2fa_and_is_verified(api, super_admin):
    login(api, super_admin.email, factories.PASSWORD)
    setup = api.post("/api/auth/otp/setup").json()
    assert setup["otpauth_url"].startswith("otpauth://totp/")
    assert "Schedule%20Confirmation" in setup["otpauth_url"]

    device = super_admin.totpdevice_set.get()
    response = api.post("/api/auth/otp/confirm", {"otp_code": factories.totp_code(device)})

    assert response.status_code == 200
    assert response.json()["otp_verified"] is True
    device.refresh_from_db()
    assert device.confirmed


def test_super_admin_with_authenticator_must_enter_a_code(api, super_admin):
    device = factories.add_totp_device(super_admin)
    state = login(api, super_admin.email, factories.PASSWORD).json()
    assert state["otp_required"] is True

    response = api.post("/api/auth/otp/verify", {"otp_code": factories.totp_code(device)})
    assert response.json()["otp_verified"] is True


def test_wrong_otp_code_is_rejected(api, super_admin):
    device = factories.add_totp_device(super_admin)
    login(api, super_admin.email, factories.PASSWORD)
    wrong = f"{(int(factories.totp_code(device)) + 1) % 1_000_000:06d}"

    response = api.post("/api/auth/otp/verify", {"otp_code": wrong})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_otp"
    assert api.get("/api/auth/me").json()["otp_verified"] is False


def test_otp_code_must_be_six_digits(api, super_admin):
    login(api, super_admin.email, factories.PASSWORD)
    response = api.post("/api/auth/otp/verify", {"otp_code": "12ab"})
    assert response.json()["fields"]["otp_code"] == ["Enter 6 digits."]


def test_cannot_replace_a_confirmed_authenticator_without_verifying(api, super_admin):
    factories.add_totp_device(super_admin)
    login(api, super_admin.email, factories.PASSWORD)

    response = api.post("/api/auth/otp/setup")

    assert response.status_code == 409
    assert response.json()["code"] == "otp_already_set_up"


@pytest.mark.parametrize(
    ("fixture", "needs_2fa"),
    [
        ("super_admin", True),
        ("support_user", True),
        ("owner", True),
        ("manager", False),
        ("office_staff", False),
    ],
)
def test_who_needs_2fa(request, api, fixture, needs_2fa):
    user = request.getfixturevalue(fixture)
    assert login(api, user.email, factories.PASSWORD).json()["requires_2fa"] is needs_2fa


def test_users_needing_2fa_are_blocked_until_verified(api, owner):
    login(api, owner.email, factories.PASSWORD)
    assert (
        api.post("/api/auth/tenant", {"tenant_id": "00000000-0000-0000-0000-000000000000"}).json()[
            "code"
        ]
        == "otp_required"
    )


def test_logout_ends_the_session(api, office_staff):
    sign_in(api, office_staff)
    assert api.post("/api/auth/logout").status_code == 204
    assert api.get("/api/auth/me").status_code == 403


def test_codes_are_throttled_after_a_wrong_one_and_the_wait_is_reported(api, super_admin):
    from datetime import timedelta

    from django.utils import timezone

    device = factories.add_totp_device(super_admin)
    login(api, super_admin.email, factories.PASSWORD)
    wrong = f"{(int(factories.totp_code(device)) + 1) % 1_000_000:06d}"
    api.post("/api/auth/otp/verify", {"otp_code": wrong})

    throttled = api.post("/api/auth/otp/verify", {"otp_code": factories.totp_code(device)})
    assert throttled.status_code == 429
    assert throttled.json()["code"] == "otp_throttled"
    assert throttled.json()["detail"] == "Too many attempts. Try again in 1 second."

    device.refresh_from_db()
    device.throttling_failure_timestamp = timezone.now() - timedelta(seconds=2)
    device.save()
    ok = api.post("/api/auth/otp/verify", {"otp_code": factories.totp_code(device)})
    assert ok.status_code == 200
    assert ok.json()["otp_verified"] is True
