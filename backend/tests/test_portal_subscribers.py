"""Subscribers (portal spec 2.2): every field rule, list filters, roles, tenancy and audit."""

import datetime as dt

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.tenancy import db_context, tenant_context
from apps.industry.models import FieldDefinition
from apps.subscribers.models import Area, Subscriber
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db

URL = "/api/portal/subscribers"
TODAY = timezone.localdate()


def payload(**overrides):
    data = {
        "customer_name": "Asha Menon",
        "mobile_number": "98470 12345",
        "external_ref": "KL07001234",
        "whatsapp_consent": True,
        "consent_date": (TODAY - dt.timedelta(days=3)).isoformat(),
        "consent_source": "paper_form",
    }
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not ...}


@pytest.fixture
def owner_api(portal):
    return portal("owner")


@pytest.fixture
def area(client_tenant):
    with tenant_context(client_tenant):
        return Area.objects.create(tenant=client_tenant, name="Edappally", code="EDP")


def make_subscriber(tenant, **fields):
    defaults = {
        "customer_name": "Existing Customer",
        "mobile_number": "+919847000001",
        "external_ref": f"REF{factories.next_id()}",
        "preferred_language": "ml",
        "whatsapp_consent": False,
    }
    defaults.update(fields)
    with tenant_context(tenant):
        return Subscriber.objects.create(tenant=tenant, **defaults)


def errors(response):
    assert response.status_code == 400, response.json()
    return response.json()["fields"]


# --- create: field rules ----------------------------------------------------------------------


def test_create_normalises_and_defaults(owner_api, client_tenant):
    response = owner_api.post(URL, payload(), format="json")
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["mobile_number"] == "+919847012345"
    assert data["preferred_language"] == client_tenant.default_language == "ml"
    assert data["status"] == "active"
    assert data["opted_out"] is False
    assert data["can_be_messaged"] is True
    assert data["custom_fields"] == {}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("customer_name", ""),
        ("customer_name", "   "),
        ("customer_name", "x" * 101),
        ("mobile_number", "12345"),
        ("mobile_number", "5847012345"),  # must start with 6-9
        ("alternate_number", "0471 2345"),
        ("external_ref", ""),
        ("external_ref", "x" * 51),
        ("preferred_language", "fr"),
        ("address", "x" * 501),
        ("status", "deleted"),
        ("consent_source", "email"),
    ],
)
def test_create_rejects_bad_values(owner_api, field, value):
    assert field in errors(owner_api.post(URL, payload(**{field: value}), format="json"))


@pytest.mark.parametrize(
    "field", ["customer_name", "mobile_number", "external_ref", "whatsapp_consent"]
)
def test_required_fields(owner_api, field):
    assert field in errors(owner_api.post(URL, payload(**{field: ...}), format="json"))


def test_alternate_number_is_normalised_or_blank(owner_api):
    data = owner_api.post(URL, payload(alternate_number="09847099999"), format="json").json()
    assert data["alternate_number"] == "+919847099999"
    data = owner_api.post(
        URL, payload(external_ref="B2", alternate_number=""), format="json"
    ).json()
    assert data["alternate_number"] == ""


def test_external_ref_is_unique_within_the_tenant_only(owner_api, client_tenant):
    make_subscriber(client_tenant, external_ref="KL07001234")
    assert "external_ref" in errors(owner_api.post(URL, payload(), format="json"))
    # Surrounding spaces don't get around it.
    assert "external_ref" in errors(
        owner_api.post(URL, payload(external_ref="  KL07001234 "), format="json")
    )
    other = factories.make_tenant("Thrissur Gas")
    make_subscriber(other, external_ref="KL07001235")
    assert owner_api.post(URL, payload(external_ref="KL07001235"), format="json").status_code == 201


def test_consent_needs_date_and_source(owner_api):
    fields = errors(
        owner_api.post(URL, payload(consent_date=..., consent_source=...), format="json")
    )
    assert set(fields) == {"consent_date", "consent_source"}


def test_consent_date_cannot_be_in_the_future(owner_api):
    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    assert "consent_date" in errors(
        owner_api.post(URL, payload(consent_date=tomorrow), format="json")
    )


def test_no_consent_clears_date_and_source(owner_api):
    data = owner_api.post(URL, payload(whatsapp_consent=False), format="json").json()
    assert data["consent_date"] is None and data["consent_source"] == ""
    assert data["can_be_messaged"] is False


def test_area_must_be_this_tenants_and_active(owner_api, client_tenant, area):
    response = owner_api.post(URL, payload(area=str(area.pk)), format="json")
    assert response.json()["area_name"] == "Edappally"

    other = factories.make_tenant("Other Agency")
    with tenant_context(other):
        foreign = Area.objects.create(tenant=other, name="Foreign")
    assert "area" in errors(
        owner_api.post(URL, payload(external_ref="B2", area=str(foreign.pk)), format="json")
    )

    with tenant_context(client_tenant):
        Area.objects.filter(pk=area.pk).update(is_active=False)
    assert "area" in errors(
        owner_api.post(URL, payload(external_ref="B3", area=str(area.pk)), format="json")
    )


def test_keeping_an_inactive_area_on_edit_is_allowed(owner_api, client_tenant, area):
    subscriber = make_subscriber(client_tenant, area=area)
    with tenant_context(client_tenant):
        Area.objects.filter(pk=area.pk).update(is_active=False)
    response = owner_api.patch(
        f"{URL}/{subscriber.pk}", {"area": str(area.pk), "address": "New address"}, format="json"
    )
    assert response.status_code == 200, response.json()


# --- opted out: system only -------------------------------------------------------------------


def test_agency_cannot_clear_opt_out(owner_api, client_tenant):
    subscriber = make_subscriber(
        client_tenant,
        opted_out=True,
        opted_out_at=timezone.now(),
        whatsapp_consent=True,
        consent_date=TODAY,
        consent_source="verbal",
    )
    response = owner_api.patch(
        f"{URL}/{subscriber.pk}", {"opted_out": False, "opted_out_at": None}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["opted_out"] is True
    assert response.json()["can_be_messaged"] is False
    with tenant_context(client_tenant):
        assert Subscriber.objects.get(pk=subscriber.pk).opted_out is True


def test_can_be_messaged_rule(client_tenant):
    base = {"whatsapp_consent": True, "consent_date": TODAY, "consent_source": "verbal"}
    assert make_subscriber(client_tenant, **base).can_be_messaged()
    assert not make_subscriber(client_tenant, **base, status="inactive").can_be_messaged()
    assert not make_subscriber(client_tenant, **base, opted_out=True).can_be_messaged()
    assert not make_subscriber(client_tenant).can_be_messaged()


# --- edit and deactivate ----------------------------------------------------------------------


def test_partial_update_keeps_consent_rules(owner_api, client_tenant):
    subscriber = make_subscriber(client_tenant)
    # Turning consent on still needs date and source.
    fields = errors(
        owner_api.patch(f"{URL}/{subscriber.pk}", {"whatsapp_consent": True}, format="json")
    )
    assert set(fields) == {"consent_date", "consent_source"}
    response = owner_api.patch(
        f"{URL}/{subscriber.pk}",
        {"whatsapp_consent": True, "consent_date": TODAY.isoformat(), "consent_source": "verbal"},
        format="json",
    )
    assert response.status_code == 200
    # Editing another field later doesn't ask for them again.
    assert (
        owner_api.patch(f"{URL}/{subscriber.pk}", {"address": "MG Road"}, format="json").status_code
        == 200
    )


def test_edit_external_ref_to_own_value_is_fine(owner_api, client_tenant):
    subscriber = make_subscriber(client_tenant, external_ref="SAME")
    response = owner_api.patch(f"{URL}/{subscriber.pk}", {"external_ref": "SAME"}, format="json")
    assert response.status_code == 200


def test_deactivate_and_no_delete(owner_api, client_tenant):
    subscriber = make_subscriber(client_tenant)
    response = owner_api.post(f"{URL}/{subscriber.pk}/deactivate")
    assert response.status_code == 200 and response.json()["status"] == "inactive"
    assert owner_api.delete(f"{URL}/{subscriber.pk}").status_code == 405
    with db_context(platform=True):
        entry = AuditLog.objects.filter(object_id=str(subscriber.pk), action="status_change").get()
    assert entry.before["status"] == "active" and entry.after["status"] == "inactive"


def test_every_write_is_audited(owner_api, client_tenant):
    created = owner_api.post(URL, payload(), format="json").json()
    owner_api.patch(f"{URL}/{created['id']}", {"address": "Kaloor"}, format="json")
    with db_context(platform=True):
        actions = list(
            AuditLog.objects.filter(object_id=created["id"])
            .order_by("created_at")
            .values_list("action", flat=True)
        )
        update = AuditLog.objects.get(object_id=created["id"], action="update")
    assert actions == ["create", "update"]
    assert update.before["address"] == "" and update.after["address"] == "Kaloor"
    assert update.tenant_id == client_tenant.pk


# --- list -------------------------------------------------------------------------------------


@pytest.fixture
def listed(client_tenant, area):
    make_subscriber(
        client_tenant,
        customer_name="Anil Kumar",
        external_ref="KL1",
        mobile_number="+919847111111",
        area=area,
    )
    make_subscriber(
        client_tenant,
        customer_name="Divya Nair",
        external_ref="KL2",
        mobile_number="+919847222222",
        whatsapp_consent=True,
        consent_date=TODAY,
        consent_source="verbal",
    )
    make_subscriber(
        client_tenant,
        customer_name="Jose Thomas",
        external_ref="TCR3",
        mobile_number="+919847333333",
        status="inactive",
        opted_out=True,
    )


def names(response):
    assert response.status_code == 200, response.json()
    return [row["customer_name"] for row in response.json()["results"]]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"search": "divya"}, ["Divya Nair"]),
        ({"search": "TCR"}, ["Jose Thomas"]),
        ({"search": "98472 22222"}, ["Divya Nair"]),
        ({"search": "+919847111111"}, ["Anil Kumar"]),
        ({"area": "none"}, ["Divya Nair", "Jose Thomas"]),
        ({"status": "inactive"}, ["Jose Thomas"]),
        ({"consent": "true"}, ["Divya Nair"]),
        ({"consent": "false"}, ["Anil Kumar", "Jose Thomas"]),
        ({"opted_out": "true"}, ["Jose Thomas"]),
    ],
)
def test_list_search_and_filters(owner_api, listed, params, expected):
    assert names(owner_api.get(URL, params)) == expected


def test_list_filters_by_area(owner_api, listed, area):
    assert names(owner_api.get(URL, {"area": str(area.pk)})) == ["Anil Kumar"]


def test_list_has_50_rows_per_page(owner_api, client_tenant):
    with tenant_context(client_tenant):
        Subscriber.objects.bulk_create(
            [
                Subscriber(
                    tenant=client_tenant,
                    customer_name=f"C{n:03}",
                    mobile_number="+919847000001",
                    external_ref=f"R{n}",
                    preferred_language="ml",
                )
                for n in range(51)
            ]
        )
    page = owner_api.get(URL).json()
    assert page["count"] == 51 and len(page["results"]) == 50 and page["next"]


def test_list_shows_only_this_tenants_subscribers(owner_api, client_tenant):
    make_subscriber(client_tenant, customer_name="Mine")
    make_subscriber(factories.make_tenant("Elsewhere"), customer_name="Theirs")
    assert names(owner_api.get(URL)) == ["Mine"]


def test_other_tenants_subscriber_is_not_found(owner_api):
    other = make_subscriber(factories.make_tenant("Elsewhere"))
    assert owner_api.get(f"{URL}/{other.pk}").status_code == 404
    assert owner_api.patch(f"{URL}/{other.pk}", {"address": "x"}, format="json").status_code == 404


# --- roles, support, suspension -------------------------------------------------------------


@pytest.mark.parametrize("role", ["owner", "manager", "office_staff"])
def test_every_client_role_can_add_and_edit(portal, role):
    api = portal(role)
    created = api.post(URL, payload(), format="json")
    assert created.status_code == 201
    assert (
        api.patch(f"{URL}/{created.json()['id']}", {"address": "x"}, format="json").status_code
        == 200
    )


def test_signed_out_and_platform_staff_without_support_get_nothing(api, super_admin, client_tenant):
    assert api.get(URL).status_code in (401, 403)
    sign_in(api, super_admin)
    assert api.get(URL).status_code == 403


def test_support_session_is_view_only(api, super_admin, client_tenant):
    from apps.platform_admin.services import start_support_session
    from apps.platform_admin.tenancy import SUPPORT_SESSION_KEY

    make_subscriber(client_tenant, customer_name="Visible")
    sign_in(api, super_admin)
    support = start_support_session(super_admin, client_tenant, "Checking an import problem")
    session = api.session
    session[SUPPORT_SESSION_KEY] = str(support.pk)
    session.save()
    assert names(api.get(URL)) == ["Visible"]
    assert api.post(URL, payload(), format="json").status_code == 403


def test_suspended_tenant_is_read_only(owner_api, client_tenant):
    from apps.platform_admin.services import suspend_tenant

    suspend_tenant(client_tenant, "Unpaid invoice for October")
    assert owner_api.get(URL).status_code == 200
    response = owner_api.post(URL, payload(), format="json")
    assert response.status_code == 403 and response.json()["code"] == "tenant_suspended"


# --- custom fields ----------------------------------------------------------------------------


@pytest.fixture
def custom_fields(gas_template):
    specs = [
        ("connection_type", "choice", False, {"options": ["Domestic", "Commercial"]}),
        ("family_size", "number", False, {}),
        ("kyc_date", "date", False, {}),
        ("landline", "phone", False, {}),
        ("dbc", "boolean", False, {}),
        ("ration_card", "text", True, {"max_length": 12}),
    ]
    for order, (key, kind, required, extra) in enumerate(specs, start=50):
        FieldDefinition.objects.create(
            template=gas_template,
            entity="subscriber",
            key=key,
            label=key.replace("_", " ").title(),
            field_type=kind,
            required=required,
            is_core=False,
            order=order,
            **extra,
        )
    FieldDefinition.objects.create(
        template=gas_template,
        entity="subscriber",
        key="old_code",
        label="Old code",
        field_type="text",
        is_core=False,
        is_active=False,
        order=99,
    )


def test_custom_fields_are_parsed_by_type(owner_api, custom_fields):
    response = owner_api.post(
        URL,
        payload(
            custom_fields={
                "connection_type": "domestic",
                "family_size": "4",
                "kyc_date": "05-03-2024",
                "landline": "9847055555",
                "dbc": "yes",
                "ration_card": " RC1234 ",
            }
        ),
        format="json",
    )
    assert response.status_code == 201, response.json()
    assert response.json()["custom_fields"] == {
        "connection_type": "Domestic",
        "family_size": 4,
        "kyc_date": "2024-03-05",
        "landline": "+919847055555",
        "dbc": True,
        "ration_card": "RC1234",
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("connection_type", "Industrial"),
        ("family_size", "four"),
        ("kyc_date", "2024-13-40"),
        ("landline", "12"),
        ("dbc", "maybe"),
        ("ration_card", "x" * 13),
        ("unknown_field", "x"),
        ("old_code", "A1"),  # switched off in the template
    ],
)
def test_custom_fields_are_validated_against_the_template(owner_api, custom_fields, key, value):
    data = payload(custom_fields={"ration_card": "RC1", key: value})
    fields = errors(owner_api.post(URL, data, format="json"))
    assert key in fields["custom_fields"]


def test_required_custom_field(owner_api, custom_fields):
    fields = errors(owner_api.post(URL, payload(custom_fields={"family_size": 3}), format="json"))
    assert "ration_card" in fields["custom_fields"]


def test_custom_fields_merge_on_edit_and_keep_switched_off_values(
    owner_api, client_tenant, custom_fields
):
    subscriber = make_subscriber(
        client_tenant, custom_fields={"ration_card": "RC1", "old_code": "A1"}
    )
    response = owner_api.patch(
        f"{URL}/{subscriber.pk}", {"custom_fields": {"family_size": 5}}, format="json"
    )
    assert response.status_code == 200, response.json()
    assert response.json()["custom_fields"] == {
        "ration_card": "RC1",
        "old_code": "A1",
        "family_size": 5,
    }
    # A blank value clears an optional field; a required one can't be cleared.
    response = owner_api.patch(
        f"{URL}/{subscriber.pk}", {"custom_fields": {"family_size": ""}}, format="json"
    )
    assert "family_size" not in response.json()["custom_fields"]
    fields = errors(
        owner_api.patch(
            f"{URL}/{subscriber.pk}", {"custom_fields": {"ration_card": ""}}, format="json"
        )
    )
    assert "ration_card" in fields["custom_fields"]


def test_portal_config_lists_fields_with_labels(owner_api, custom_fields):
    data = owner_api.get("/api/portal/config").json()
    keys = {f["key"]: f for f in data["subscriber_fields"]}
    assert keys["external_ref"]["label"] == "Consumer number"
    assert keys["ration_card"]["required"] is True
    assert "old_code" not in keys
    assert data["default_language"] == "ml"


def test_staff_member_name_rules(portal):
    api = portal("manager")
    assert api.post("/api/portal/staff", {"name": "B"}, format="json").status_code == 400
    response = api.post(
        "/api/portal/staff", {"name": "Biju K", "phone": "98470 11111"}, format="json"
    )
    assert response.status_code == 201 and response.json()["phone"] == "+919847011111"
    assert api.post("/api/portal/staff", {"name": "biju k"}, format="json").status_code == 400
    assert (
        api.post("/api/portal/staff", {"name": "Shaji", "phone": "123"}, format="json").status_code
        == 400
    )
