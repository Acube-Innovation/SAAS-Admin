"""WhatsApp templates (spec 2.7) and the Workflow 3.4 state machine, with the stub provider."""

import socket

import pytest
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from apps.audit.models import AuditLog
from apps.core.exceptions import ApiError
from apps.core.tenancy import db_context, tenant_context
from apps.industry.services import assign_template
from apps.messaging_templates import services
from apps.messaging_templates.models import (
    VersionStatus,
    WhatsAppTemplate,
    WhatsAppTemplateVersion,
)
from apps.messaging_templates.providers import StubProvider
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db

URL = "/api/admin/whatsapp-templates"
S = VersionStatus
BODY = (
    "Hello {{customer_name}}, this is {{agency_name}}. Your {{cylinder_type}} gas cylinder "
    "(booking {{booking_reference}}) is scheduled for delivery on {{scheduled_date}}. "
    "Will someone be available to receive it?"
)
SAMPLES = {
    "customer_name": "Asha",
    "agency_name": "Kochi Gas Agency",
    "cylinder_type": "14.2 kg domestic",
    "booking_reference": "BK-1042",
    "scheduled_date": "28 Oct 2026",
}
BUTTONS = [
    {"text": "Yes, available", "outcome": "confirmed"},
    {"text": "Not available", "outcome": "not_available"},
    {"text": "Change date", "outcome": "reschedule"},
]


def pdf(name="signoff.pdf", size=None):
    content = b"%PDF-1.4\n% client sign-off by email\n" + (b"0" * (size or 100))
    return SimpleUploadedFile(name, content, content_type="application/pdf")


@pytest.fixture
def client_tenant(tenant, gas_template):
    assign_template(tenant, gas_template)
    tenant.save()
    return tenant


@pytest.fixture
def delivery_type(gas_template):
    return gas_template.schedule_types.get(key="cylinder_delivery")


def content(**overrides):
    data = {
        "body": BODY,
        "footer": "Reply STOP to stop these messages.",
        "buttons": BUTTONS,
        "sample_values": SAMPLES,
    }
    data.update(overrides)
    return data


@pytest.fixture
def version(client_tenant, delivery_type, super_admin):
    return services.create_template(
        client_tenant,
        {
            "name": "delivery_reminder",
            "language": "en",
            "schedule_type": delivery_type,
            **content(),
        },
        super_admin,
    )


def set_status(version, status, **extra):
    with tenant_context(version.tenant_id):
        WhatsAppTemplateVersion.objects.filter(pk=version.pk).update(status=status, **extra)
        version.refresh_from_db()
    return version


def reload(version):
    with tenant_context(version.tenant_id):
        version.refresh_from_db()
    return version


@pytest.fixture
def admin_api(api, super_admin):
    sign_in(api, super_admin)
    return api


def create_payload(tenant, schedule_type, **overrides):
    return {
        "tenant": str(tenant.pk),
        "name": "delivery_reminder",
        "language": "en",
        "schedule_type": str(schedule_type.pk),
        **content(),
        **overrides,
    }


# --- the state machine: every allowed and blocked change ---------------------------------------


ACTIONS = {
    "edit": lambda v, u: services.edit_version(v, {"body": BODY + " Thanks."}, u["admin"]),
    "send_to_client": lambda v, u: services.send_to_client(v, u["admin"]),
    "client_approve": lambda v, u: services.client_approve(v, u["owner"]),
    "client_send_back": lambda v, u: services.client_send_back(v, u["owner"], "Use our brand name"),
    "manual_signoff": lambda v, u: services.manual_signoff(v, u["admin"], pdf()),
    "submit": lambda v, u: services.submit(v, u["admin"]),
    "make_live": lambda v, u: services.make_live(v, u["admin"]),
    "disable": lambda v, u: services.disable(v, u["admin"], "No longer used"),
    "new_version": lambda v, u: services.new_version(v, u["admin"]),
}
TARGETS = {
    "edit": S.DRAFT,
    "send_to_client": S.SENT_TO_CLIENT,
    "client_approve": S.SIGNED_OFF,
    "client_send_back": S.SENT_BACK,
    "manual_signoff": S.SIGNED_OFF,
    "submit": S.SUBMITTED,
    "make_live": S.APPROVED,
    "disable": S.DISABLED,
    "new_version": S.DRAFT,
}
ALLOWED_FROM = {
    "edit": {S.DRAFT, S.SENT_BACK},
    "send_to_client": {S.DRAFT},
    "client_approve": {S.SENT_TO_CLIENT},
    "client_send_back": {S.SENT_TO_CLIENT},
    "manual_signoff": {S.SENT_TO_CLIENT},
    "submit": {S.SIGNED_OFF},
    "make_live": {S.APPROVED},
    "disable": set(S.values) - {S.DISABLED},
    "new_version": {S.APPROVED, S.REJECTED},
}


def test_the_matrix_matches_the_service_table():
    for action, allowed in ALLOWED_FROM.items():
        assert services.TRANSITIONS[action][0] == allowed, action
        assert services.TRANSITIONS[action][1] == TARGETS[action], action


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("status", S.values)
def test_every_state_change(version, super_admin, owner, status, action):
    set_status(version, status)
    users = {"admin": super_admin, "owner": owner}

    if status in ALLOWED_FROM[action]:
        result = ACTIONS[action](version, users)
        assert result.status == TARGETS[action]
        if action == "new_version":
            assert result.version == 2 and result.pk != version.pk
        if action == "make_live":
            assert result.is_live
    else:
        with pytest.raises(ApiError) as exc:
            ACTIONS[action](version, users)
        assert exc.value.get_codes() == "invalid_transition"
        assert exc.value.status_code == 409
        assert reload(version).status == status  # unchanged


PROVIDER_CHANGES = [
    # provider status, version status before -> after (None = no change)
    ("approved", {S.SUBMITTED: S.APPROVED, S.PAUSED: S.APPROVED}),
    ("rejected", {S.SUBMITTED: S.REJECTED}),
    ("paused", {S.APPROVED: S.PAUSED}),
    ("pending", {}),
    ("disabled", {s: S.DISABLED for s in S.values if s != S.DISABLED}),
]


@pytest.mark.parametrize(("provider_status", "changes"), PROVIDER_CHANGES)
@pytest.mark.parametrize("status", S.values)
def test_every_provider_driven_change(version, status, provider_status, changes):
    set_status(version, status, provider_template_id="stub-matrix")
    StubProvider.set_status("stub-matrix", provider_status, reason="Wording looks promotional")

    result = services.sync_status(version)

    assert result.status == changes.get(status, status)
    if result.status == S.REJECTED and status == S.SUBMITTED:
        assert result.rejection_reason == "Wording looks promotional"


def test_make_live_twice_is_refused(version, super_admin):
    set_status(version, S.APPROVED)
    services.make_live(version, super_admin)
    with pytest.raises(ApiError, match="already live"):
        services.make_live(version, super_admin)


def test_new_version_is_refused_while_another_is_in_progress(version, super_admin):
    set_status(version, S.APPROVED)
    services.new_version(version, super_admin)
    with pytest.raises(ApiError) as exc:
        services.new_version(version, super_admin)
    assert exc.value.get_codes() == "open_version_exists"


def test_only_one_live_version_per_template_in_the_database(version, super_admin):
    set_status(version, S.APPROVED, is_live=True)
    second = services.new_version(version, super_admin)
    with pytest.raises(IntegrityError), tenant_context(version.tenant_id), transaction.atomic():
        WhatsAppTemplateVersion.objects.filter(pk=second.pk).update(status=S.APPROVED, is_live=True)


def test_client_send_back_needs_a_comment(version, owner, super_admin):
    services.send_to_client(version, super_admin)
    with pytest.raises(ApiError, match="Say what should change"):
        services.client_send_back(version, owner, "no")


def test_every_change_writes_an_event_and_an_audit_entry(version, super_admin, owner):
    services.send_to_client(version, super_admin)
    services.client_send_back(version, owner, "Mention our phone number")
    services.edit_version(version, {"footer": "Reply STOP to opt out."}, super_admin)

    with tenant_context(version.tenant_id):
        events = list(version.events.values_list("action", "from_status", "to_status", "note"))
    assert events == [
        ("create", "", "draft", ""),
        ("send_to_client", "draft", "sent_to_client", ""),
        ("client_send_back", "sent_to_client", "sent_back", "Mention our phone number"),
        ("edit", "sent_back", "draft", ""),
    ]
    with db_context(platform=True):
        actions = list(
            AuditLog.objects.filter(object_id=str(version.pk))
            .order_by("created_at")
            .values_list("action", flat=True)
        )
    assert actions == ["create", "status_change", "status_change", "update"]


# --- happy path through the API ----------------------------------------------------------------


def test_full_lifecycle_through_the_api(admin_api, client_tenant, delivery_type):
    created = admin_api.post(URL, create_payload(client_tenant, delivery_type), format="json")
    assert created.status_code == 201, created.json()
    template_id = created.json()["id"]
    v1 = created.json()["versions"][0]
    assert (v1["version"], v1["status"]) == (1, "draft")
    assert "send_to_client" in v1["available_actions"]
    base = f"{URL}/{template_id}/versions/{v1['id']}"

    assert (
        admin_api.patch(base, {"footer": "Reply STOP to opt out."}, format="json").json()["footer"]
        == "Reply STOP to opt out."
    )
    assert admin_api.post(f"{base}/send-to-client").json()["status"] == "sent_to_client"

    signed = admin_api.post(
        f"{base}/manual-signoff", {"evidence": pdf(), "note": "Approved by email on 26 Oct"}
    ).json()
    assert (signed["status"], signed["signoff_method"], signed["has_evidence"]) == (
        "signed_off",
        "manual",
        True,
    )
    version = WhatsAppTemplateVersion.objects.none()
    with tenant_context(client_tenant):
        version = WhatsAppTemplateVersion.objects.get(pk=v1["id"])
    assert default_storage.exists(version.signoff_evidence.name)
    assert version.signoff_evidence.name.startswith(f"tenants/{client_tenant.pk}/whatsapp-signoff/")
    assert admin_api.get(f"{base}/evidence").json()["url"]

    submitted = admin_api.post(f"{base}/submit").json()
    assert submitted["status"] == "submitted"
    provider_id = submitted["provider_template_id"]
    assert StubProvider.submitted(provider_id)["name"] == "delivery_reminder"

    assert admin_api.post(f"{base}/sync-status").json()["status"] == "submitted"  # still pending
    StubProvider.set_status(provider_id, "approved")
    assert admin_api.post(f"{base}/sync-status").json()["status"] == "approved"
    assert admin_api.post(f"{base}/make-live").json()["is_live"] is True

    v2 = admin_api.post(f"{base}/new-version").json()
    assert (v2["version"], v2["status"], v2["body"]) == (2, "draft", BODY)
    base2 = f"{URL}/{template_id}/versions/{v2['id']}"
    admin_api.post(f"{base2}/send-to-client")
    admin_api.post(f"{base2}/manual-signoff", {"evidence": pdf()})
    provider_id_2 = admin_api.post(f"{base2}/submit").json()["provider_template_id"]
    StubProvider.set_status(provider_id_2, "approved")
    admin_api.post(f"{base2}/sync-status")
    admin_api.post(f"{base2}/make-live")

    detail = admin_api.get(f"{URL}/{template_id}").json()
    assert [(v["version"], v["status"], v["is_live"]) for v in detail["versions"]] == [
        (2, "approved", True),
        (1, "approved", False),
    ]
    assert detail["live_version"] == 2


def test_rejection_reason_is_shown_and_a_new_version_can_follow(admin_api, version, super_admin):
    set_status(version, S.SIGNED_OFF)
    services.submit(version, super_admin)
    provider_id = reload(version).provider_template_id
    StubProvider.set_status(
        provider_id, "rejected", reason="Promotional wording in a utility template"
    )

    base = f"{URL}/{version.template_id}/versions/{version.pk}"
    rejected = admin_api.post(f"{base}/sync-status").json()

    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"] == "Promotional wording in a utility template"
    assert rejected["available_actions"] == ["disable", "new_version"]
    assert admin_api.post(f"{base}/new-version").status_code == 201


def test_approved_content_cannot_be_edited(admin_api, version):
    set_status(version, S.APPROVED)
    response = admin_api.patch(
        f"{URL}/{version.template_id}/versions/{version.pk}", {"body": "Changed"}, format="json"
    )
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


def test_list_filters(admin_api, version, client_tenant):
    other = factories.make_tenant("Other")
    assert admin_api.get(URL, {"tenant": str(client_tenant.pk)}).json()["count"] == 1
    assert admin_api.get(URL, {"tenant": str(other.pk)}).json()["count"] == 0
    assert admin_api.get(URL, {"status": "draft"}).json()["count"] == 1
    assert admin_api.get(URL, {"status": "approved"}).json()["count"] == 0
    row = admin_api.get(URL).json()["results"][0]
    assert row["latest_version"]["status"] == "draft"
    assert row["tenant_name"] == "Kochi Gas Agency"


def test_variables_picker_is_limited_to_the_schedule_types_fields(admin_api, delivery_type):
    variables = admin_api.get(f"{URL}/variables", {"schedule_type": str(delivery_type.pk)}).json()[
        "variables"
    ]
    assert {"agency_name", "customer_name", "booking_reference", "cylinder_type"} <= set(variables)
    assert "amount_due" not in variables


# --- roles -------------------------------------------------------------------------------------


def test_support_staff_draft_and_send_but_cannot_sign_off_submit_or_publish(
    api, support_user, client_tenant, delivery_type
):
    sign_in(api, support_user)
    created = api.post(URL, create_payload(client_tenant, delivery_type), format="json").json()
    base = f"{URL}/{created['id']}/versions/{created['versions'][0]['id']}"

    assert api.patch(base, {"footer": "Reply STOP to opt out."}, format="json").status_code == 200
    assert api.post(f"{base}/send-to-client").status_code == 200
    assert api.post(f"{base}/manual-signoff", {"evidence": pdf()}).status_code == 403
    assert api.post(f"{base}/submit").status_code == 403
    assert api.post(f"{base}/make-live").status_code == 403
    assert api.post(f"{base}/disable").status_code == 403


@pytest.mark.parametrize("role", ["owner", "manager", "office_staff"])
def test_tenant_users_cannot_use_the_admin_template_api(request, api, role):
    sign_in(api, request.getfixturevalue(role))
    assert api.get(URL).status_code == 403


# --- field rules (spec 2.7) --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"name": "Delivery Reminder"}, "name", "Lowercase letters"),
        ({"name": "delivery-reminder"}, "name", "Lowercase letters"),
        ({"body": "x" * 1025, "sample_values": {}}, "body", "At most 1024 characters"),
        ({"body": "", "sample_values": {}}, "body", "The body is required"),
        ({"header_text": "x" * 61}, "header_text", "At most 60 characters"),
        (
            {"header_text": "{{customer_name}} {{agency_name}}"},
            "header_text",
            "at most one variable",
        ),
        ({"footer": "x" * 61}, "footer", "At most 60 characters"),
        ({"footer": "Call {{agency_name}}"}, "footer", "can't contain variables"),
        (
            {"buttons": [*BUTTONS, {"text": "Call me", "outcome": "reschedule"}]},
            "buttons",
            "At most 3 quick-reply buttons",
        ),
        (
            {"buttons": [{"text": "Yes, I will definitely be home", "outcome": "confirmed"}]},
            "buttons",
            "1-25 characters",
        ),
        ({"buttons": [{"text": "Yes", "outcome": "maybe"}]}, "buttons", "valid choice"),
        (
            {
                "buttons": [
                    {"text": "Yes", "outcome": "confirmed"},
                    {"text": "Yes", "outcome": "confirmed"},
                ]
            },
            "buttons",
            "must be unique",
        ),
        (
            {"body": "Your loan {{loan_amount}} is due", "sample_values": {"loan_amount": "1"}},
            "body",
            "Unknown variables: loan_amount",
        ),
        ({"body": "Hello {{1}}", "sample_values": {}}, "body", "Write variables as {{field_key}}"),
        ({"sample_values": {**SAMPLES, "cylinder_type": ""}}, "sample_values", "cylinder_type"),
    ],
)
def test_invalid_content_is_rejected(
    admin_api, client_tenant, delivery_type, overrides, field, message
):
    response = admin_api.post(
        URL, create_payload(client_tenant, delivery_type, **overrides), format="json"
    )
    assert response.status_code == 400, response.json()
    assert message in str(response.json()["fields"][field])
    assert not WhatsAppTemplate.objects.exists()


def test_limits_themselves_are_allowed(admin_api, client_tenant, delivery_type):
    body = "x" * 1024
    response = admin_api.post(
        URL,
        create_payload(
            client_tenant,
            delivery_type,
            body=body,
            header_text="h" * 60,
            footer="f" * 60,
            buttons=[{"text": "y" * 25, "outcome": "confirmed"}],
            sample_values={},
        ),
        format="json",
    )
    assert response.status_code == 201, response.json()


def test_name_is_unique_per_client_and_language(admin_api, client_tenant, delivery_type):
    assert (
        admin_api.post(URL, create_payload(client_tenant, delivery_type), format="json").status_code
        == 201
    )
    duplicate = admin_api.post(URL, create_payload(client_tenant, delivery_type), format="json")
    malayalam = admin_api.post(
        URL, create_payload(client_tenant, delivery_type, language="ml"), format="json"
    )
    assert duplicate.json()["code"] == "name_taken"
    assert malayalam.status_code == 201


def test_schedule_type_must_come_from_the_clients_template(admin_api, client_tenant, gas_template):
    from apps.industry.models import IndustryTemplate, ScheduleType

    other = IndustryTemplate.objects.create(key="cable_operator", name="Cable")
    bill_due = ScheduleType.objects.create(template=other, key="bill_due", name="Bill due")
    response = admin_api.post(
        URL, create_payload(client_tenant, bill_due, sample_values={}), format="json"
    )
    assert response.status_code == 400
    assert "industry template" in response.json()["detail"]


@pytest.mark.parametrize(
    ("upload", "message"),
    [
        (lambda: pdf(size=5 * 1024 * 1024), "5 MB or smaller"),
        (lambda: SimpleUploadedFile("signoff.exe", b"MZ...."), "PDF, PNG or JPG"),
        (lambda: SimpleUploadedFile("signoff.png", b"%PDF-1.4 not a png"), "doesn't match"),
    ],
)
def test_signoff_evidence_rules(admin_api, version, upload, message):
    set_status(version, S.SENT_TO_CLIENT)
    response = admin_api.post(
        f"{URL}/{version.template_id}/versions/{version.pk}/manual-signoff", {"evidence": upload()}
    )
    assert response.status_code == 400
    assert message in str(response.json()["fields"]["evidence"])
    assert reload(version).status == S.SENT_TO_CLIENT


def test_png_and_jpg_evidence_are_accepted(version, super_admin):
    set_status(version, S.SENT_TO_CLIENT)
    png = SimpleUploadedFile("signoff.png", b"\x89PNG\r\n\x1a\n" + b"0" * 50)
    assert services.manual_signoff(version, super_admin, png).status == S.SIGNED_OFF


# --- provider, tenancy, scheduling -------------------------------------------------------------


def test_provider_is_the_stub_by_default():
    assert settings.WHATSAPP_PROVIDER == "apps.messaging_templates.providers.StubProvider"


def test_no_network_calls_are_made(monkeypatch, version, super_admin):
    """Submit and sync with outbound connections blocked (local database sockets allowed)."""
    real_connect = socket.socket.connect

    def guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost", "::1") and not str(host).startswith("/"):
            raise AssertionError(f"Unexpected network call to {address}")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(
        socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("net"))
    )

    set_status(version, S.SIGNED_OFF)
    services.submit(version, super_admin)
    StubProvider.set_status(reload(version).provider_template_id, "approved")
    assert services.sync_status(reload(version)).status == S.APPROVED


def test_templates_are_isolated_between_tenants(version, client_tenant):
    other = factories.make_tenant("Other")
    with tenant_context(other):
        assert not WhatsAppTemplate.objects.exists()
    with tenant_context(client_tenant):
        assert WhatsAppTemplate.objects.count() == 1


def test_beat_task_syncs_every_waiting_version(version, super_admin):
    from apps.messaging_templates.tasks import sync_template_statuses

    set_status(version, S.SIGNED_OFF)
    services.submit(version, super_admin)
    StubProvider.set_status(reload(version).provider_template_id, "approved")

    assert sync_template_statuses.delay().get() == 1
    assert reload(version).status == S.APPROVED
    assert settings.CELERY_BEAT_SCHEDULE["sync-whatsapp-template-statuses"]["schedule"] == 600.0
