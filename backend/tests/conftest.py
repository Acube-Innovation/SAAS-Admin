import pytest
from rest_framework.test import APIClient

from apps.accounts.models import PlatformRole, TenantRole
from tests import factories


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def tenant(db):
    return factories.make_tenant("Kochi Gas Agency")


@pytest.fixture
def super_admin(db):
    return factories.make_user("admin@example.com", platform_role=PlatformRole.SUPER_ADMIN)


@pytest.fixture
def support_user(db):
    return factories.make_user("support@example.com", platform_role=PlatformRole.SUPPORT)


@pytest.fixture
def owner(tenant):
    user = factories.make_user("owner@example.com")
    factories.add_membership(user, tenant, TenantRole.OWNER)
    return user


@pytest.fixture
def manager(tenant):
    user = factories.make_user("manager@example.com")
    factories.add_membership(user, tenant, TenantRole.MANAGER)
    return user


@pytest.fixture
def office_staff(tenant):
    user = factories.make_user("staff@example.com")
    factories.add_membership(user, tenant, TenantRole.OFFICE_STAFF)
    return user


def sign_in(api, user, password=factories.PASSWORD, with_2fa=True):
    """Log in through the API; complete 2FA when the user needs it and `with_2fa` is set."""
    response = api.post("/api/auth/login", {"email": user.email, "password": password})
    assert response.status_code == 200, response.json()
    if with_2fa and response.json()["requires_2fa"]:
        device = factories.add_totp_device(user)
        response = api.post("/api/auth/otp/verify", {"otp_code": factories.totp_code(device)})
        assert response.status_code == 200, response.json()
    return response
