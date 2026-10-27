"""Roles and permissions: IsPlatformStaff, IsSuperAdmin, tenant roles, tenant resolution."""

import pytest

from apps.accounts.models import Membership, TenantRole
from apps.core.tenancy import tenant_context, user_context
from tests import factories
from tests.conftest import sign_in

pytestmark = [pytest.mark.django_db, pytest.mark.urls("tests.urls_permissions")]

ROLES = ["super_admin", "support_user", "owner", "manager", "office_staff"]

EXPECTED = {
    # view: roles allowed (all others get 403)
    "/t/staff": {"super_admin", "support_user"},
    "/t/super-admin": {"super_admin"},
    "/t/owner": {"owner"},
    "/t/owner-or-manager": {"owner", "manager"},
    "/t/any-member": {"owner", "manager", "office_staff"},
}


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("url", EXPECTED)
def test_role_matrix(request, api, role, url):
    sign_in(api, request.getfixturevalue(role))

    response = api.get(url)

    expected = 200 if role in EXPECTED[url] else 403
    assert response.status_code == expected, (role, url, response.json())


@pytest.mark.parametrize("url", EXPECTED)
def test_anonymous_users_are_refused_everywhere(api, url):
    assert api.get(url).status_code == 403


@pytest.mark.parametrize("role", ["super_admin", "support_user"])
def test_platform_staff_need_completed_2fa_for_admin_views(request, api, role):
    sign_in(api, request.getfixturevalue(role), with_2fa=False)

    assert api.get("/t/staff").status_code == 403
    assert api.get("/t/super-admin").status_code == 403


def test_owner_needs_completed_2fa_for_tenant_views(api, owner):
    sign_in(api, owner, with_2fa=False)
    assert api.get("/t/owner").json()["code"] == "otp_required"


def test_deactivated_member_loses_access(api, tenant, manager):
    with tenant_context(tenant):
        Membership.objects.filter(user=manager).update(status=Membership.Status.DEACTIVATED)
    sign_in(api, manager)

    assert api.get("/t/any-member").status_code == 403


# --- tenant resolution -------------------------------------------------------------------------


def test_single_membership_is_the_active_tenant(api, tenant, manager):
    sign_in(api, manager)
    assert api.get("/t/any-member").json()["tenant_id"] == str(tenant.id)


def test_user_with_two_memberships_picks_a_tenant(api, tenant, manager):
    second = factories.make_tenant("Second Agency")
    factories.add_membership(manager, second, TenantRole.OWNER)
    sign_in(api, manager)

    me = api.get("/api/auth/me").json()
    assert {m["tenant_name"] for m in me["memberships"]} == {"Kochi Gas Agency", "Second Agency"}
    assert me["active_tenant_id"] is None

    api.post("/api/auth/tenant", {"tenant_id": str(second.id)})
    assert api.get("/t/owner").json()["tenant_id"] == str(second.id)
    assert api.get("/t/any-member", HTTP_X_TENANT_ID=str(tenant.id)).json()["tenant_id"] == str(
        tenant.id
    )


def test_cannot_select_or_request_a_tenant_without_membership(api, tenant, manager):
    other = factories.make_tenant("Other Agency")
    sign_in(api, manager)

    assert api.post("/api/auth/tenant", {"tenant_id": str(other.id)}).status_code == 403
    assert api.get("/t/any-member", HTTP_X_TENANT_ID=str(other.id)).status_code == 403


def test_users_see_only_their_own_memberships_in_other_tenants(tenant, manager, owner):
    other = factories.make_tenant("Other Agency")
    stranger = factories.make_user("stranger@example.com")
    factories.add_membership(stranger, other, TenantRole.OWNER)

    with user_context(manager):
        visible = set(Membership.objects.values_list("user__email", flat=True))
    # Own row only: not the owner's row in the same tenant, not the stranger's in another tenant.
    assert visible == {"manager@example.com"}

    with tenant_context(tenant):
        same_tenant = set(Membership.objects.values_list("user__email", flat=True))
    assert same_tenant == {"manager@example.com", "owner@example.com"}
