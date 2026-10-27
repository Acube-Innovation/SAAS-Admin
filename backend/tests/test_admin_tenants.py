"""Clients API (spec 2.3 to 2.5, Workflow 3.1): create, validation, roles, list, edit."""

import pytest
from django.core import mail

from apps.accounts.models import Invite, Membership, User
from apps.audit.models import AuditLog
from apps.core.models import Tenant
from apps.core.tenancy import db_context, tenant_context
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db

URL = "/api/admin/tenants"


_TEMPLATE = {}


@pytest.fixture(autouse=True)
def _template_id(gas_template):
    _TEMPLATE["id"] = str(gas_template.pk)


def payload(**overrides):
    data = {
        "industry_template": _TEMPLATE.get("id"),
        "name": "Kollam Gas Agency",
        "legal_name": "Kollam Gas Agency Pvt Ltd",
        "gstin": "32ABCDE1234F1Z5",
        "address_line1": "Chinnakada Road",
        "city": "Kollam",
        "state": "KL",
        "pin_code": "691001",
        "contact_name": "Suresh Pillai",
        "contact_phone": "98470 12345",
        "contact_email": "office@kollamgas.test",
        "owner_name": "Suresh Pillai",
        "owner_email": "Suresh@KollamGas.test",
    }
    data.update(overrides)
    return data


@pytest.fixture
def admin_api(api, super_admin, gas_template):
    sign_in(api, super_admin)
    return api


@pytest.fixture
def support_api(api, support_user):
    sign_in(api, support_user)
    return api


def audit_actions(tenant_id):
    with db_context(platform=True):
        return list(
            AuditLog.objects.filter(tenant_id=tenant_id)
            .order_by("created_at")
            .values_list("action", "object_type")
        )


# --- Workflow 3.1: create client ---------------------------------------------------------------


def test_create_client_creates_tenant_owner_membership_and_invite(
    admin_api, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_api.post(URL, payload(), format="json")

    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["status"] == "active"
    assert data["daily_send_cap"] == 5000  # default
    assert data["default_language"] == "ml"
    assert data["contact_phone"] == "+919847012345"  # normalised
    assert data["owner_email"] == "suresh@kollamgas.test"

    tenant = Tenant.objects.get(pk=data["id"])
    owner = User.objects.get(email="suresh@kollamgas.test")
    assert owner.full_name == "Suresh Pillai"
    assert not owner.has_usable_password()
    with tenant_context(tenant):
        membership = Membership.objects.get(tenant=tenant)
        assert (membership.user_id, membership.role, membership.status) == (
            owner.id,
            "owner",
            "invited",
        )
        assert Invite.objects.filter(membership=membership, used_at=None).count() == 1
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["suresh@kollamgas.test"]
    assert audit_actions(tenant.id) == [
        ("create", "core.tenant"),
        ("create", "accounts.user"),
        ("create", "accounts.membership"),
        ("create", "accounts.invite"),
    ]


def test_create_client_is_one_transaction(admin_api, monkeypatch):
    def broken_invite(*args, **kwargs):
        raise RuntimeError("mail system down")

    monkeypatch.setattr("apps.platform_admin.services.create_invite", broken_invite)
    with pytest.raises(RuntimeError):
        admin_api.post(URL, payload(), format="json")

    assert not Tenant.objects.filter(name="Kollam Gas Agency").exists()
    assert not User.objects.filter(email="suresh@kollamgas.test").exists()


def test_existing_user_becomes_owner_without_a_new_account(admin_api, tenant, manager):
    response = admin_api.post(
        URL, payload(owner_email="MANAGER@example.com", gstin=""), format="json"
    )

    assert response.status_code == 201
    assert User.objects.filter(email="manager@example.com").count() == 1
    assert {m.tenant.name for m in manager.memberships()} == {
        "Kochi Gas Agency",
        "Kollam Gas Agency",
    }


# --- field validation (spec 2.4) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("gstin", "32ABCDE1234F1Y5", "valid 15-character GSTIN"),
        ("gstin", "32ABCDE1234", "valid 15-character GSTIN"),
        ("gstin", "29ABCDE1234F1Z5", "does not belong to the selected state"),
        ("state", "XX", "not a valid choice"),
        ("pin_code", "091001", "6-digit PIN"),
        ("pin_code", "69100", "6-digit PIN"),
        ("contact_phone", "12345", "valid Indian mobile"),
        ("contact_phone", "+91 5847012345", "valid Indian mobile"),
        ("owner_phone", "0000000000", "valid Indian mobile"),
        ("contact_email", "not-an-email", "valid email"),
        ("owner_email", "", "may not be blank"),
        ("name", "K", "at least 2 characters"),
        ("legal_name", "x" * 201, "no more than 200"),
        ("daily_send_cap", 0, "greater than or equal to 1"),
        ("daily_send_cap", 100_001, "less than or equal to 100000"),
        ("default_language", "fr", "not a valid choice"),
    ],
)
def test_invalid_fields_are_rejected(admin_api, field, value, message):
    response = admin_api.post(URL, payload(**{field: value}), format="json")

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "validation_error"
    assert message in " ".join(body["fields"][field])
    assert not Tenant.objects.exists()


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("9847012345", "+919847012345"),
        ("+91 98470-12345", "+919847012345"),
        ("919847012345", "+919847012345"),
        ("09847012345", "+919847012345"),
    ],
)
def test_phone_numbers_are_normalised(admin_api, raw, stored):
    response = admin_api.post(URL, payload(contact_phone=raw, gstin=""), format="json")
    assert response.json()["contact_phone"] == stored


def test_gstin_is_uppercased_and_unique(admin_api):
    first = admin_api.post(URL, payload(gstin="32abcde1234f1z5"), format="json")
    assert first.json()["gstin"] == "32ABCDE1234F1Z5"

    second = admin_api.post(
        URL, payload(name="Another", owner_email="b@example.com"), format="json"
    )
    assert second.status_code == 400
    assert second.json()["fields"]["gstin"] == ["Another client already uses this GSTIN."]


def test_gstin_is_optional_and_blank_is_stored_as_null(admin_api):
    for n in range(2):
        response = admin_api.post(
            URL,
            payload(name=f"No GST {n}", gstin="", owner_email=f"o{n}@example.com"),
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["gstin"] is None


# --- roles -------------------------------------------------------------------------------------


def test_support_staff_can_read_but_not_create_or_edit(support_api, tenant):
    assert support_api.get(URL).status_code == 200
    assert support_api.get(f"{URL}/{tenant.id}").status_code == 200
    assert support_api.post(URL, payload(), format="json").status_code == 403
    assert support_api.patch(f"{URL}/{tenant.id}", {"city": "Kottayam"}).status_code == 403


@pytest.mark.parametrize("role", ["owner", "manager", "office_staff"])
def test_tenant_users_cannot_use_the_admin_api(request, api, role):
    sign_in(api, request.getfixturevalue(role))
    assert api.get(URL).status_code == 403


def test_super_admin_without_2fa_is_refused(api, super_admin):
    sign_in(api, super_admin, with_2fa=False)
    assert api.get(URL).status_code == 403


def test_anonymous_is_refused(api):
    assert api.get(URL).status_code == 403


# --- list, search, filter ----------------------------------------------------------------------


def test_list_shows_owner_email_and_paginates(admin_api, tenant, owner):
    for n in range(26):
        factories.make_tenant(f"Agency {n:02d}")

    page = admin_api.get(URL).json()

    assert page["count"] == 27
    assert len(page["results"]) == 25
    kochi = next(t for t in admin_api.get(URL, {"search": "Kochi"}).json()["results"])
    assert kochi["owner_email"] == "owner@example.com"


def test_search_matches_name_gstin_and_owner_email(admin_api, tenant, owner):
    factories.make_tenant("Thrissur Gas", gstin="32ABCDE1234F1Z5")

    def names(**params):
        return [t["name"] for t in admin_api.get(URL, params).json()["results"]]

    assert names(search="thrissur") == ["Thrissur Gas"]
    assert names(search="32ABCDE") == ["Thrissur Gas"]
    assert names(search="owner@example") == ["Kochi Gas Agency"]


def test_filter_by_status_and_ordering(admin_api, tenant):
    factories.make_tenant("Zeta Agency", status=Tenant.Status.SUSPENDED)

    assert [t["name"] for t in admin_api.get(URL, {"status": "suspended"}).json()["results"]] == [
        "Zeta Agency"
    ]
    assert admin_api.get(URL, {"ordering": "-name"}).json()["results"][0]["name"] == "Zeta Agency"


# --- edit (spec 2.5 Details / Settings) --------------------------------------------------------


def test_super_admin_edits_details_and_the_change_is_audited(admin_api, tenant):
    response = admin_api.patch(
        f"{URL}/{tenant.id}",
        {"city": "Ernakulam", "daily_send_cap": 8000, "timezone": "Asia/Kolkata"},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["daily_send_cap"] == 8000
    with db_context(platform=True):
        entry = AuditLog.objects.get(tenant=tenant, action="update")
    assert (entry.before["city"], entry.after["city"]) == ("Kochi", "Ernakulam")


def test_edit_validates_like_create(admin_api, tenant):
    other = factories.make_tenant("Other", gstin="32ABCDE1234F1Z5")

    clash = admin_api.patch(f"{URL}/{tenant.id}", {"gstin": other.gstin}, format="json")
    wrong_state = admin_api.patch(f"{URL}/{tenant.id}", {"gstin": "29ABCDE1234F1Z5"}, format="json")
    bad_zone = admin_api.patch(f"{URL}/{tenant.id}", {"timezone": "Mars/Base"}, format="json")

    assert clash.json()["fields"]["gstin"] == ["Another client already uses this GSTIN."]
    assert "selected state" in wrong_state.json()["fields"]["gstin"][0]
    assert bad_zone.json()["fields"]["timezone"] == ["Unknown time zone."]
    assert admin_api.patch(f"{URL}/{other.id}", {"city": "Kollam"}).status_code == 200  # own GSTIN


def test_status_cannot_be_changed_through_edit(admin_api, tenant):
    admin_api.patch(f"{URL}/{tenant.id}", {"status": "suspended"}, format="json")
    tenant.refresh_from_db()
    assert tenant.status == "active"


def test_openapi_schema_lists_the_admin_tenant_endpoints(api):
    schema = api.get("/api/schema").content.decode()
    for path in (
        "/api/admin/tenants:",
        "/api/admin/tenants/{id}:",
        "/api/admin/tenants/{id}/suspend:",
        "/api/admin/tenants/{id}/activate:",
        "/api/admin/tenants/{id}/users:",
        "/api/admin/tenants/{id}/users/{membership_id}/resend-invite:",
        "/api/admin/tenants/{id}/activity:",
        "/api/admin/tenants/{id}/support-sessions:",
        "/api/admin/support-sessions/{id}/end:",
    ):
        assert path in schema, path


def test_reference_data_lists_states_with_gst_codes_and_languages(admin_api):
    data = admin_api.get("/api/admin/reference-data").json()
    kerala = next(s for s in data["states"] if s["code"] == "KL")
    assert kerala == {"code": "KL", "name": "Kerala", "gst_code": "32"}
    assert len(data["states"]) == 36
    assert {"code": "ml", "name": "Malayalam"} in data["languages"]
    assert data["daily_send_cap"] == {"default": 5000, "min": 1, "max": 100_000}


def test_reference_data_needs_platform_staff(api, owner):
    sign_in(api, owner)
    assert api.get("/api/admin/reference-data").status_code == 403


def test_detail_includes_the_clients_copy_of_template_defaults(admin_api):
    created = admin_api.post(URL, payload(), format="json").json()
    detail = admin_api.get(f"{URL}/{created['id']}").json()
    rule = detail["industry_settings"]["schedule_types"]["cylinder_delivery"]["reminder_rule"]
    assert rule["send_time"] == "10:00"
