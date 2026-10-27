"""Industry templates (spec 2.6), Workflow 3.5 and the gas_agency seed."""

import copy

import pytest

from apps.audit.models import AuditLog
from apps.core.models import Tenant
from apps.core.tenancy import db_context
from apps.industry.models import FieldDefinition, IndustryTemplateVersion
from apps.industry.services import definition_of, tenant_config
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db

URL = "/api/admin/industry-templates"


@pytest.fixture
def admin_api(api, super_admin, gas_template):
    sign_in(api, super_admin)
    return api


@pytest.fixture
def definition(gas_template):
    """The editor's current definition, ready to modify and PUT back."""
    return copy.deepcopy(definition_of(gas_template))


@pytest.fixture
def client_using(gas_template, admin_api):
    """A client created from the template through the API, so the template is in use."""
    response = admin_api.post(
        "/api/admin/tenants",
        {
            "industry_template": str(gas_template.pk),
            "name": "Kochi Gas Agency",
            "legal_name": "Kochi Gas Agency Pvt Ltd",
            "address_line1": "MG Road",
            "city": "Kochi",
            "state": "KL",
            "pin_code": "682016",
            "contact_name": "Asha",
            "contact_phone": "9847000001",
            "contact_email": "office@kochigas.test",
            "owner_name": "Asha Nair",
            "owner_email": "asha@kochigas.test",
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    return Tenant.objects.get(pk=response.json()["id"])


def save(api, template, definition):
    return api.put(f"{URL}/{template.pk}", definition, format="json")


def schedule_fields(definition):
    return definition["schedule_types"][0]["fields"]


def new_field(key, required=False, field_type="text", **extra):
    return {
        "key": key,
        "label": key.replace("_", " ").title(),
        "field_type": field_type,
        "required": required,
        "is_active": True,
        **extra,
    }


# --- the seed ----------------------------------------------------------------------------------


def test_seeded_gas_agency_template_is_returned_by_the_api(admin_api, gas_template):
    [template] = admin_api.get(URL).json()
    assert (template["key"], template["name"], template["current_version"]) == (
        "gas_agency",
        "Gas agency",
        1,
    )

    detail = admin_api.get(f"{URL}/{gas_template.pk}").json()
    [delivery] = detail["schedule_types"]
    assert delivery["name"] == "Cylinder delivery"
    assert delivery["id"] == str(gas_template.schedule_types.get().pk)
    fields = {f["key"]: f for f in delivery["fields"]}
    assert fields["cylinder_type"]["options"] == [
        "14.2 kg domestic",
        "5 kg",
        "10 kg composite",
        "19 kg commercial",
        "47.5 kg commercial",
    ]
    assert set(fields) == {
        "booking_reference",
        "booking_date",
        "scheduled_date",
        "cylinder_type",
        "quantity",
        "remarks",
    }
    assert {f["key"] for f in detail["subscriber_fields"]} >= {
        "customer_name",
        "mobile_number",
        "external_ref",
        "whatsapp_consent",
    }
    rule = delivery["default_reminder_rule"]
    assert (rule["send_time"], rule["wait_minutes"], rule["quiet_start"], rule["quiet_end"]) == (
        "10:00",
        240,
        "21:00",
        "08:00",
    )
    assert [(a["label"], a["outcome"]) for a in delivery["default_question_flow"]["answers"]] == [
        ("Yes, available", "confirmed"),
        ("Not available", "not_available"),
        ("Change date", "reschedule"),
    ]


def test_seed_is_idempotent(gas_template):
    from apps.industry.seed import seed_gas_agency

    assert seed_gas_agency().pk == gas_template.pk
    assert IndustryTemplateVersion.objects.filter(template=gas_template).count() == 1


# --- Workflow 3.5 ------------------------------------------------------------------------------


def test_each_save_creates_a_new_audited_version(admin_api, gas_template, definition):
    definition["description"] = "LPG distributors in Kerala"

    response = save(admin_api, gas_template, definition)

    assert response.status_code == 200
    assert response.json()["current_version"] == 2
    versions = admin_api.get(f"{URL}/{gas_template.pk}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["definition"]["description"] == "LPG distributors in Kerala"
    assert versions[0]["created_by_email"] == "admin@example.com"
    with db_context(platform=True):
        entry = AuditLog.objects.get(action="update", object_type="industry.industrytemplate")
    assert (entry.before["version"], entry.after["version"]) == (1, 2)
    assert entry.after["description"] == "LPG distributors in Kerala"


def test_new_optional_field_applies_to_all_clients(
    admin_api, gas_template, definition, client_using
):
    schedule_fields(definition).append(new_field("delivery_slot", options=[], field_type="text"))

    assert save(admin_api, gas_template, definition).status_code == 200

    keys = [f["key"] for f in tenant_config(client_using)["schedule_types"][0]["fields"]]
    assert "delivery_slot" in keys


def test_removed_field_is_switched_off_and_kept(admin_api, gas_template, definition, client_using):
    definition["schedule_types"][0]["fields"] = [
        f for f in schedule_fields(definition) if f["key"] != "quantity"
    ]

    assert save(admin_api, gas_template, definition).status_code == 200

    field = FieldDefinition.objects.get(template=gas_template, key="quantity")
    assert field.is_active is False  # kept, not deleted
    keys = [f["key"] for f in tenant_config(client_using)["schedule_types"][0]["fields"]]
    assert "quantity" not in keys
    detail = admin_api.get(f"{URL}/{gas_template.pk}").json()
    quantity = next(f for f in detail["schedule_types"][0]["fields"] if f["key"] == "quantity")
    assert quantity["is_active"] is False


def test_default_changes_apply_to_new_clients_only(
    admin_api, gas_template, definition, client_using
):
    delivery = definition["schedule_types"][0]
    delivery["default_reminder_rule"]["send_time"] = "09:00"
    delivery["default_question_flow"]["answers"][0]["label"] = "Yes, I'll be home"
    cylinder = next(f for f in delivery["fields"] if f["key"] == "cylinder_type")
    cylinder["options"] = [*cylinder["options"], "2 kg FTL"]
    assert save(admin_api, gas_template, definition).status_code == 200

    existing = tenant_config(client_using)["schedule_types"][0]
    assert existing["reminder_rule"]["send_time"] == "10:00"
    assert existing["question_flow"]["answers"][0]["label"] == "Yes, available"
    existing_cylinder = next(f for f in existing["fields"] if f["key"] == "cylinder_type")
    assert "2 kg FTL" not in existing_cylinder["options"]

    response = admin_api.post(
        "/api/admin/tenants",
        {
            "industry_template": str(gas_template.pk),
            "name": "Kannur Gas",
            "legal_name": "Kannur Gas LLP",
            "address_line1": "Fort Road",
            "city": "Kannur",
            "state": "KL",
            "pin_code": "670001",
            "contact_name": "Rahul",
            "contact_phone": "9847000003",
            "contact_email": "office@kannurgas.test",
            "owner_name": "Rahul",
            "owner_email": "rahul@kannurgas.test",
        },
        format="json",
    )
    new_client = Tenant.objects.get(pk=response.json()["id"])
    assert new_client.industry_template_version == 2
    new = tenant_config(new_client)["schedule_types"][0]
    assert new["reminder_rule"]["send_time"] == "09:00"
    assert new["question_flow"]["answers"][0]["label"] == "Yes, I'll be home"
    assert "2 kg FTL" in next(f for f in new["fields"] if f["key"] == "cylinder_type")["options"]


def test_new_required_field_is_blocked_once_a_client_uses_the_template(
    admin_api, gas_template, definition, client_using
):
    schedule_fields(definition).append(new_field("gas_book_number", required=True))

    response = save(admin_api, gas_template, definition)

    assert response.status_code == 409
    assert response.json()["code"] == "required_field_in_use"
    assert not FieldDefinition.objects.filter(key="gas_book_number").exists()
    gas_template.refresh_from_db()
    assert gas_template.current_version == 1  # nothing saved


def test_new_required_field_is_allowed_while_no_client_uses_the_template(
    admin_api, gas_template, definition
):
    schedule_fields(definition).append(new_field("gas_book_number", required=True))
    assert save(admin_api, gas_template, definition).status_code == 200
    assert FieldDefinition.objects.get(key="gas_book_number").required


def test_optional_field_cannot_become_required_once_in_use(
    admin_api, gas_template, definition, client_using
):
    schedule_fields(definition).append(new_field("delivery_slot"))
    assert save(admin_api, gas_template, definition).status_code == 200

    next(f for f in schedule_fields(definition) if f["key"] == "delivery_slot")["required"] = True
    response = save(admin_api, gas_template, definition)

    assert response.status_code == 409
    assert response.json()["code"] == "required_field_in_use"


def test_switched_off_required_field_cannot_come_back_once_in_use(
    admin_api, gas_template, definition, client_using
):
    original = copy.deepcopy(definition)
    definition["schedule_types"][0]["fields"] = [
        f for f in schedule_fields(definition) if f["key"] != "quantity"
    ]
    assert save(admin_api, gas_template, definition).status_code == 200

    response = save(admin_api, gas_template, original)  # quantity is required

    assert response.json()["code"] == "required_field_in_use"


def test_field_type_cannot_change_once_in_use(admin_api, gas_template, definition, client_using):
    quantity = next(f for f in schedule_fields(definition) if f["key"] == "quantity")
    quantity["field_type"] = "text"

    response = save(admin_api, gas_template, definition)

    assert response.status_code == 409
    assert response.json()["code"] == "field_type_locked"


def test_template_key_can_never_change(admin_api, gas_template, definition):
    definition["key"] = "lpg_agency"
    response = save(admin_api, gas_template, definition)
    assert response.json()["code"] == "key_immutable"


@pytest.mark.parametrize(
    "change",
    [
        lambda f: f.update(field_type="number"),
        lambda f: f.update(required=False),
        lambda f: f.update(is_active=False),
    ],
)
def test_core_fields_only_allow_label_changes(admin_api, gas_template, definition, change):
    booking_ref = next(f for f in schedule_fields(definition) if f["key"] == "booking_reference")
    change(booking_ref)
    assert save(admin_api, gas_template, definition).json()["code"] == "core_field_locked"


def test_core_field_label_can_change(admin_api, gas_template, definition, client_using):
    consumer = next(f for f in definition["subscriber_fields"] if f["key"] == "external_ref")
    consumer["label"] = "LPG consumer number"

    assert save(admin_api, gas_template, definition).status_code == 200
    assert FieldDefinition.objects.get(key="external_ref").label == "LPG consumer number"


def test_core_field_cannot_be_removed(admin_api, gas_template, definition):
    definition["subscriber_fields"] = [
        f for f in definition["subscriber_fields"] if f["key"] != "mobile_number"
    ]
    assert save(admin_api, gas_template, definition).json()["code"] == "core_field_locked"


# --- editor validation (spec 2.6) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "where"),
    [
        (lambda d: d.update(key="Gas"), "key"),
        (lambda d: d.update(name="G"), "name"),
        (lambda d: schedule_fields(d).append(new_field("Bad Key")), "schedule_types"),
        (lambda d: schedule_fields(d).append(new_field("quantity")), "schedule_types"),
        (
            lambda d: schedule_fields(d).append(
                new_field("x_opts", options=["a", "a"], field_type="choice")
            ),
            "schedule_types",
        ),
        (
            lambda d: d["schedule_types"][0]["default_reminder_rule"].update(send_time="25:00"),
            "schedule_types",
        ),
        (
            lambda d: d["schedule_types"][0]["default_reminder_rule"].update(wait_minutes=10),
            "schedule_types",
        ),
        (
            lambda d: d["schedule_types"][0]["default_question_flow"]["answers"][0].update(
                label="Yes, someone will be available"
            ),
            "schedule_types",
        ),
        (
            lambda d: d["schedule_types"][0]["default_question_flow"]["answers"][0].update(
                outcome="maybe"
            ),
            "schedule_types",
        ),
    ],
)
def test_invalid_definitions_are_rejected(admin_api, gas_template, definition, mutate, where):
    mutate(definition)
    response = save(admin_api, gas_template, definition)
    assert response.status_code == 400
    assert where in response.json()["fields"]


def test_choice_field_needs_options(admin_api, gas_template, definition):
    schedule_fields(definition).append(new_field("slot", field_type="choice", options=[]))
    response = save(admin_api, gas_template, definition)
    assert response.status_code == 400
    assert "needs options" in response.json()["detail"]


def test_create_a_new_industry_template(admin_api):
    body = {
        "key": "cable_operator",
        "name": "Cable operator",
        "subscriber_fields": [
            new_field("customer_name", required=True),
            new_field("mobile_number", required=True, field_type="phone"),
        ],
        "schedule_types": [
            {
                "key": "bill_due",
                "name": "Bill due",
                "fields": [new_field("amount_due", required=True, field_type="number")],
                "default_reminder_rule": {
                    "send_time": "11:00",
                    "days_before": 2,
                    "wait_minutes": 240,
                    "quiet_start": "21:00",
                    "quiet_end": "08:00",
                    "delivery_weekdays": [0, 1, 2, 3, 4, 5, 6],
                    "channel_order": ["whatsapp"],
                },
                "default_question_flow": {
                    "answers": [{"key": "will_pay", "label": "I will pay", "outcome": "confirmed"}]
                },
            }
        ],
    }

    response = admin_api.post(URL, body, format="json")

    assert response.status_code == 201, response.json()
    assert response.json()["current_version"] == 1
    assert admin_api.post(URL, body, format="json").json()["code"] == "key_taken"


# --- roles -------------------------------------------------------------------------------------


def test_support_staff_can_read_but_not_edit(api, support_user, gas_template, definition):
    sign_in(api, support_user)
    assert api.get(URL).status_code == 200
    assert api.get(f"{URL}/{gas_template.pk}/versions").status_code == 200
    assert save(api, gas_template, definition).status_code == 403
    assert api.post(URL, definition, format="json").status_code == 403


@pytest.mark.parametrize("role", ["owner", "manager", "office_staff"])
def test_tenant_users_cannot_reach_the_editor(request, api, role):
    sign_in(api, request.getfixturevalue(role))
    assert api.get(URL).status_code == 403


# --- create client uses the template (Workflow 3.1 step 2) -------------------------------------


def test_create_client_requires_an_active_template(admin_api, gas_template):
    body = {
        "name": "No Template Agency",
        "legal_name": "No Template Agency LLP",
        "address_line1": "Road",
        "city": "Kochi",
        "state": "KL",
        "pin_code": "682016",
        "contact_name": "X",
        "contact_phone": "9847000009",
        "contact_email": "x@example.com",
        "owner_name": "X",
        "owner_email": "x@example.com",
    }
    missing = admin_api.post("/api/admin/tenants", body, format="json")
    assert missing.json()["fields"]["industry_template"] == ["This field is required."]

    gas_template.is_active = False
    gas_template.save()
    inactive = admin_api.post(
        "/api/admin/tenants", {**body, "industry_template": str(gas_template.pk)}, format="json"
    )
    assert inactive.json()["fields"]["industry_template"] == ["Choose an active industry template."]


def test_new_client_gets_a_copy_of_the_template_defaults(client_using, gas_template):
    assert client_using.industry_template_id == gas_template.pk
    assert client_using.industry_template_version == 1
    copied = client_using.settings["industry"]
    assert copied["schedule_types"]["cylinder_delivery"]["reminder_rule"]["send_time"] == "10:00"
    assert copied["choice_options"]["cylinder_delivery.cylinder_type"][0] == "14.2 kg domestic"


def test_clients_list_shows_and_filters_by_template(admin_api, client_using):
    factories.make_tenant("Template-less")
    rows = admin_api.get("/api/admin/tenants", {"industry_template": "gas_agency"}).json()[
        "results"
    ]
    assert [(r["name"], r["industry_template_name"]) for r in rows] == [
        ("Kochi Gas Agency", "Gas agency")
    ]


def test_template_in_use_counts_clients(admin_api, gas_template, client_using):
    assert admin_api.get(f"{URL}/{gas_template.pk}").json()["clients_using"] == 1
