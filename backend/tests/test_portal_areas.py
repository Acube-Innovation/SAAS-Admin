"""Areas and staff (portal spec 2.7): rules, roles, tenancy and audit."""

import pytest

from apps.audit.models import AuditLog
from apps.core.tenancy import db_context, tenant_context
from apps.jobs.models import StaffMember
from apps.subscribers.models import Area
from tests import factories

pytestmark = pytest.mark.django_db

AREAS = "/api/portal/areas"
STAFF = "/api/portal/staff"


@pytest.fixture
def manager_api(portal):
    return portal("manager")


@pytest.fixture
def staff_member(client_tenant):
    with tenant_context(client_tenant):
        return StaffMember.objects.create(tenant=client_tenant, name="Biju K")


def test_create_area_with_default_staff(manager_api, staff_member):
    response = manager_api.post(
        AREAS,
        {"name": " Edappally ", "code": " EDP ", "default_staff": str(staff_member.pk)},
        format="json",
    )
    assert response.status_code == 201, response.json()
    data = response.json()
    assert (data["name"], data["code"], data["default_staff_name"]) == (
        "Edappally",
        "EDP",
        "Biju K",
    )
    assert data["is_active"] is True


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"name": "E"}, "name"),
        ({"name": "x" * 101}, "name"),
        ({"name": "Kaloor", "code": "x" * 21}, "code"),
    ],
)
def test_area_field_rules(manager_api, body, field):
    response = manager_api.post(AREAS, body, format="json")
    assert response.status_code == 400 and field in response.json()["fields"]


def test_area_name_unique_within_tenant_ignoring_case(manager_api, client_tenant):
    assert manager_api.post(AREAS, {"name": "Kaloor"}, format="json").status_code == 201
    response = manager_api.post(AREAS, {"name": "KALOOR"}, format="json")
    assert response.status_code == 400 and "name" in response.json()["fields"]
    other = factories.make_tenant("Other")
    with tenant_context(other):
        Area.objects.create(tenant=other, name="Vyttila")
    assert manager_api.post(AREAS, {"name": "Vyttila"}, format="json").status_code == 201


def test_default_staff_must_be_this_tenants_and_active(manager_api, client_tenant, staff_member):
    other = factories.make_tenant("Other")
    with tenant_context(other):
        foreign = StaffMember.objects.create(tenant=other, name="Foreign")
    response = manager_api.post(
        AREAS, {"name": "A1", "default_staff": str(foreign.pk)}, format="json"
    )
    assert "default_staff" in response.json()["fields"]
    with tenant_context(client_tenant):
        StaffMember.objects.filter(pk=staff_member.pk).update(is_active=False)
    response = manager_api.post(
        AREAS, {"name": "A2", "default_staff": str(staff_member.pk)}, format="json"
    )
    assert "default_staff" in response.json()["fields"]


def test_deactivate_area_is_audited_as_status_change(manager_api, client_tenant):
    area = manager_api.post(AREAS, {"name": "Kakkanad"}, format="json").json()
    response = manager_api.patch(f"{AREAS}/{area['id']}", {"is_active": False}, format="json")
    assert response.json()["is_active"] is False
    assert manager_api.delete(f"{AREAS}/{area['id']}").status_code == 405
    with db_context(platform=True):
        actions = set(
            AuditLog.objects.filter(object_id=area["id"]).values_list("action", flat=True)
        )
    assert actions == {"create", "status_change"}


def test_active_filter_and_subscriber_counts(manager_api, client_tenant):
    from apps.subscribers.models import Subscriber

    with tenant_context(client_tenant):
        live = Area.objects.create(tenant=client_tenant, name="Live")
        Area.objects.create(tenant=client_tenant, name="Old", is_active=False)
        Subscriber.objects.create(
            tenant=client_tenant,
            customer_name="A",
            mobile_number="+919847000001",
            external_ref="R1",
            preferred_language="ml",
            area=live,
        )
    rows = manager_api.get(AREAS).json()
    assert [(r["name"], r["subscriber_count"]) for r in rows] == [("Live", 1), ("Old", 0)]
    assert [r["name"] for r in manager_api.get(AREAS, {"active": "true"}).json()] == ["Live"]


def test_office_staff_can_list_areas_but_not_change_them(portal):
    api = portal("office_staff")
    assert api.get(AREAS).status_code == 200
    assert api.post(AREAS, {"name": "Kaloor"}, format="json").status_code == 403
    assert api.get(STAFF).status_code == 403


def test_only_this_tenants_areas_and_staff_are_listed(manager_api, client_tenant):
    other = factories.make_tenant("Other")
    with tenant_context(other):
        Area.objects.create(tenant=other, name="Theirs")
        StaffMember.objects.create(tenant=other, name="Their driver")
    assert manager_api.get(AREAS).json() == []
    assert manager_api.get(STAFF).json()["results"] == []


def test_staff_deactivate_and_active_filter(manager_api):
    member = manager_api.post(STAFF, {"name": "Shaji P"}, format="json").json()
    manager_api.patch(f"{STAFF}/{member['id']}", {"is_active": False}, format="json")
    assert manager_api.get(STAFF, {"active": "true"}).json()["results"] == []
    assert manager_api.delete(f"{STAFF}/{member['id']}").status_code == 405
