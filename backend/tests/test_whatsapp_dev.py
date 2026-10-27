"""Development-only Simulate controls and browser-signed file links."""

import pytest

from apps.core.tenancy import tenant_context
from apps.industry.services import assign_template
from apps.messaging_templates import services
from apps.messaging_templates.models import VersionStatus, WhatsAppTemplateVersion
from tests.conftest import sign_in
from tests.test_whatsapp_templates import BODY, BUTTONS, SAMPLES

pytestmark = pytest.mark.django_db
S = VersionStatus


@pytest.fixture
def version(tenant, gas_template, super_admin, owner):
    assign_template(tenant, gas_template)
    tenant.save()
    return services.create_template(
        tenant,
        {
            "name": "delivery_reminder",
            "language": "en",
            "schedule_type": gas_template.schedule_types.get(key="cylinder_delivery"),
            "body": BODY,
            "buttons": BUTTONS,
            "sample_values": SAMPLES,
        },
        super_admin,
    )


def url(version):
    return f"/api/admin/dev/whatsapp-versions/{version.pk}/simulate"


def status_of(version):
    with tenant_context(version.tenant_id):
        return WhatsAppTemplateVersion.objects.get(pk=version.pk).status


@pytest.fixture
def admin_api(api, super_admin):
    sign_in(api, super_admin)
    return api


def test_hidden_unless_debug(admin_api, version):
    """Test settings run with DEBUG off, like staging and production."""
    assert admin_api.post(url(version), {"action": "client_approve"}).status_code == 404
    assert admin_api.get("/api/admin/reference-data").json()["whatsapp_dev_controls"] is False


def test_hidden_with_a_real_provider_even_in_debug(admin_api, version, settings):
    settings.DEBUG = True
    settings.WHATSAPP_PROVIDER = "apps.messaging_templates.providers.MetaCloudProvider"
    assert admin_api.post(url(version), {"action": "client_approve"}).status_code == 404


def test_hidden_when_switched_off(admin_api, version, settings):
    settings.DEBUG = True
    settings.WHATSAPP_DEV_CONTROLS = False
    assert admin_api.post(url(version), {"action": "client_approve"}).status_code == 404


def test_super_admins_only(api, support_user, version, settings):
    settings.DEBUG = True
    sign_in(api, support_user)
    assert api.post(url(version), {"action": "client_approve"}).status_code == 403


def test_simulates_the_client(admin_api, version, super_admin, settings):
    settings.DEBUG = True
    assert admin_api.get("/api/admin/reference-data").json()["whatsapp_dev_controls"] is True
    services.send_to_client(version, super_admin)

    sent_back = admin_api.post(
        url(version), {"action": "client_send_back", "reason": "Add our phone number"}
    )
    assert sent_back.json()["status"] == "sent_back"
    assert sent_back.json()["events"][-1]["note"] == "Add our phone number"
    assert sent_back.json()["events"][-1]["actor_email"] == "owner@example.com"

    services.edit_version(version, {"footer": "Call 0484 200 1000"}, super_admin)
    services.send_to_client(version, super_admin)
    approved = admin_api.post(url(version), {"action": "client_approve"}).json()
    assert (approved["status"], approved["signoff_method"]) == ("signed_off", "client")


def test_simulates_meta_and_syncs(admin_api, version, super_admin, settings):
    settings.DEBUG = True
    services.send_to_client(version, super_admin)
    with tenant_context(version.tenant_id):
        WhatsAppTemplateVersion.objects.filter(pk=version.pk).update(status=S.SIGNED_OFF)
    assert (
        admin_api.post(url(version), {"action": "provider_approve"}).json()["code"]
        == "not_submitted"
    )

    services.submit(version, super_admin)
    rejected = admin_api.post(
        url(version), {"action": "provider_reject", "reason": "Promotional wording"}
    )
    assert (rejected.json()["status"], rejected.json()["rejection_reason"]) == (
        "rejected",
        "Promotional wording",
    )


def test_signed_links_use_the_public_endpoint(settings, monkeypatch):
    from storages.backends.s3 import S3Storage

    from apps.core import storage

    s3 = S3Storage(
        bucket_name="schedule-saas-local",
        access_key="key",
        secret_key="secret",
        endpoint_url="http://storage:8333",
        region_name="ap-south-1",
        addressing_style="path",
        signature_version="s3v4",
    )
    monkeypatch.setattr(storage, "default_storage", s3)
    settings.AWS_STORAGE_BUCKET_NAME = "schedule-saas-local"
    settings.AWS_S3_PUBLIC_ENDPOINT_URL = "http://localhost:8333"

    link = storage.signed_url("tenants/t1/whatsapp-signoff/v1/signoff.pdf")

    assert link.startswith("http://localhost:8333/schedule-saas-local/tenants/t1/")
    assert "X-Amz-Expires=300" in link
    assert "storage:8333" not in link


def test_signed_links_fall_back_to_the_storage_url(settings):
    from apps.core import storage

    settings.AWS_S3_PUBLIC_ENDPOINT_URL = None
    assert storage.signed_url("a.pdf").endswith("a.pdf")
