import pytest
from rest_framework.test import APIClient

from apps.accounts.models import PlatformRole, TenantRole
from tests import factories


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def gas_template(db):
    """The seeded gas_agency template (re-seeded if a transactional test flushed it)."""
    from apps.industry.seed import seed_gas_agency

    return seed_gas_agency()


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


# --- client portal --------------------------------------------------------------------------


@pytest.fixture
def client_tenant(tenant, gas_template):
    """The default tenant as a gas agency client (industry template assigned)."""
    from apps.industry.services import assign_template

    assign_template(tenant, gas_template)
    tenant.save()
    return tenant


@pytest.fixture
def portal(api, client_tenant):
    """Sign in to the portal as a given role of client_tenant: portal("manager") -> APIClient."""

    def as_role(role="owner", tenant=None):
        user = factories.make_user(f"{role}-{factories.next_id()}@agency.test")
        factories.add_membership(user, tenant or client_tenant, role)
        sign_in(api, user)
        return api

    return as_role
