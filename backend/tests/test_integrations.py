"""Integrations (portal spec 2.10): API keys, signed webhooks with retries, the public API."""

import datetime as dt
import hashlib
import hmac
import http.server
import io
import json
import socket
import threading
import urllib.error

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.core.tenancy import db_context, tenant_context
from apps.integrations import webhooks
from apps.integrations.models import ApiKey, WebhookDelivery
from tests import factories
from tests.conftest import sign_in

pytestmark = pytest.mark.django_db
KEYS = "/api/portal/integrations/api-keys"
HOOKS = "/api/portal/integrations/webhooks"
DELIVERIES = "/api/portal/integrations/deliveries"
TODAY = timezone.localdate()


def new_key(api, name="Billing system"):
    response = api.post(KEYS, {"name": name}, format="json")
    assert response.status_code == 201, response.json()
    return response.json()


def public(raw):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def receiver_verify(secret, body: bytes, header: str) -> bool:
    """What a client's own server does, written from the documented scheme (not our code)."""
    parts = dict(p.split("=", 1) for p in header.split(","))
    expected = hmac.new(
        secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])


SUBSCRIBER = {
    "customer_name": "Asha Menon",
    "mobile_number": "9847012345",
    "external_ref": "KL0991",
    "preferred_language": "ml",
    "whatsapp_consent": True,
    "consent_date": str(TODAY),
    "consent_source": "paper_form",
}


# --- API keys ---------------------------------------------------------------------------------


def test_key_is_shown_once_and_stored_hashed(portal, client_tenant):
    api = portal("owner")
    created = new_key(api)
    raw = created["secret"]
    assert raw.startswith(f"sck_{client_tenant.pk.hex}_")
    assert (
        created["key"]["prefix"].startswith("sck_…") and raw[-10:] not in created["key"]["prefix"]
    )
    listed = api.get(KEYS).json()
    assert "secret" not in listed[0] and raw not in json.dumps(listed)
    with tenant_context(client_tenant):
        stored = ApiKey.objects.get()
    assert (
        stored.key_hash == hashlib.sha256(raw.encode()).hexdigest() and raw not in stored.key_hash
    )
    with db_context(platform=True):
        entry = AuditLog.objects.get(object_type="integrations.apikey", action="create")
    assert raw not in json.dumps(entry.after) and "key_hash" not in entry.after


def test_revoked_keys_are_rejected_at_once(portal, client_tenant):
    api = portal("owner")
    created = new_key(api)
    client = public(created["secret"])
    assert client.get("/api/v1/subscribers").status_code == 200
    assert api.post(f"{KEYS}/{created['key']['id']}/revoke").json()["revoked_at"]
    response = client.get("/api/v1/subscribers")
    assert response.status_code == 401 and response.json()["code"] == "invalid_api_key"
    assert api.post(f"{KEYS}/{created['key']['id']}/revoke").status_code == 409


@pytest.mark.parametrize("role", ["manager", "office_staff"])
def test_integrations_are_owner_only(portal, role):
    api = portal(role)
    assert api.get(KEYS).status_code == 403
    assert api.post(KEYS, {"name": "x y"}).status_code == 403
    assert api.get(HOOKS).status_code == 403


def test_support_sessions_cannot_see_integrations(api, super_admin, client_tenant):
    from apps.platform_admin.services import start_support_session
    from apps.platform_admin.tenancy import SUPPORT_SESSION_KEY

    sign_in(api, super_admin)
    support = start_support_session(super_admin, client_tenant, "Checking API set-up")
    session = api.session
    session[SUPPORT_SESSION_KEY] = str(support.pk)
    session.save()
    assert api.get(KEYS).status_code == 403
    assert api.get(DELIVERIES).status_code == 403


# --- public API -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [None, "Token abc", "Bearer nonsense", "Bearer sck_" + "0" * 32 + "_secret"],
)
def test_public_api_needs_a_valid_key(header):
    client = APIClient()
    if header:
        client.credentials(HTTP_AUTHORIZATION=header)
    response = client.get("/api/v1/subscribers")
    assert response.status_code == 401
    assert response.json()["code"] in ("api_key_required", "invalid_api_key")


def test_public_api_subscribers_schedules_and_outcomes(portal, client_tenant):
    client = public(new_key(portal("owner"))["secret"])
    created = client.post("/api/v1/subscribers", SUBSCRIBER, format="json")
    assert created.status_code == 201, created.json()
    assert client.get("/api/v1/subscribers/KL0991").json()["customer_name"] == "Asha Menon"
    patched = client.patch(
        "/api/v1/subscribers/KL0991", {"address": "Near the temple"}, format="json"
    )
    assert patched.json()["address"] == "Near the temple"

    booking = client.post(
        "/api/v1/schedules",
        {
            "subscriber_ref": "KL0991",
            "booking_reference": "BK-API-1",
            "booking_date": str(TODAY),
            "custom_fields": {"cylinder_type": "14.2 kg domestic", "quantity": 1},
        },
        format="json",
    )
    assert booking.status_code == 201, booking.json()
    new_date = str(TODAY + dt.timedelta(days=2))
    dated = client.post(
        "/api/v1/schedules/BK-API-1/set-date", {"scheduled_date": new_date}, format="json"
    )
    assert dated.status_code == 200 and dated.json()["scheduled_date"] == new_date

    from apps.schedules import services
    from apps.schedules.models import Schedule

    before = timezone.now() - dt.timedelta(seconds=1)
    with tenant_context(client_tenant):
        services.record_outcome(Schedule.objects.get(booking_reference="BK-API-1"), "confirmed")
    outcomes = client.get("/api/v1/outcomes", {"since": before.isoformat()}).json()["results"]
    assert outcomes == [
        {
            "booking_reference": "BK-API-1",
            "subscriber_ref": "KL0991",
            "outcome": "confirmed",
            "new_date_requested": None,
            "recorded_at": outcomes[0]["recorded_at"],
        }
    ]
    assert client.get("/api/v1/outcomes", {"since": "yesterday"}).status_code == 400
    cancelled = client.post(
        "/api/v1/schedules/BK-API-1/cancel", {"reason": "Customer moved away"}, format="json"
    )
    assert cancelled.json()["status"] == "cancelled"
    with db_context(platform=True):
        entry = AuditLog.objects.filter(object_type="subscribers.subscriber", action="create").get()
    assert entry.actor_id is None and entry.tenant_id == client_tenant.pk


def test_keys_only_reach_their_own_agency(portal, client_tenant):
    raw = new_key(portal("owner"))["secret"]
    other = factories.make_tenant("Other Agency")
    forged = raw.replace(client_tenant.pk.hex, other.pk.hex)
    assert public(forged).get("/api/v1/subscribers").status_code == 401
    with tenant_context(other):
        from apps.subscribers.models import Subscriber

        Subscriber.objects.create(
            tenant=other,
            customer_name="Theirs",
            mobile_number="+919847000999",
            external_ref="THEIRS",
        )
    client = public(raw)
    assert client.get("/api/v1/subscribers").json()["count"] == 0
    assert client.get("/api/v1/subscribers/THEIRS").status_code == 404


def test_rate_limit_per_key(portal, client_tenant):
    created = new_key(portal("owner"))
    with tenant_context(client_tenant):
        ApiKey.objects.filter(pk=created["key"]["id"]).update(rate_limit_per_minute=3)
    client = public(created["secret"])
    statuses = [client.get("/api/v1/subscribers").status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    limited = client.get("/api/v1/subscribers")
    assert limited.json()["code"] == "rate_limited" and int(limited["Retry-After"]) <= 60


def test_suspended_agency_reads_only(portal, client_tenant):
    from apps.platform_admin.services import suspend_tenant

    client = public(new_key(portal("owner"))["secret"])
    suspend_tenant(client_tenant, "Unpaid invoice for October")
    assert client.get("/api/v1/subscribers").status_code == 200
    response = client.post("/api/v1/subscribers", SUBSCRIBER, format="json")
    assert response.status_code == 403 and response.json()["code"] == "tenant_suspended"


# --- webhooks ---------------------------------------------------------------------------------


def test_endpoint_rules(portal):
    api = portal("owner")
    assert (
        api.post(
            HOOKS, {"url": "https://example.com/hook", "events": []}, format="json"
        ).status_code
        == 400
    )
    assert (
        api.post(
            HOOKS, {"url": "https://example.com/hook", "events": ["booking.deleted"]}, format="json"
        ).status_code
        == 400
    )
    for n in range(5):
        assert (
            api.post(
                HOOKS,
                {"url": f"https://example.com/{n}", "events": ["booking.created"]},
                format="json",
            ).status_code
            == 201
        )
    assert (
        api.post(
            HOOKS, {"url": "https://example.com/6", "events": ["booking.created"]}, format="json"
        ).json()["code"]
        == "too_many_endpoints"
    )


def test_urls_must_be_public_https_outside_development(settings, monkeypatch):
    settings.DEBUG = False
    settings.TESTING = False
    with pytest.raises(Exception, match="HTTPS"):
        webhooks.check_url("http://example.com/hook")
    for internal in ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.10"]:
        monkeypatch.setattr(
            socket, "getaddrinfo", lambda *a, ip=internal, **k: [(0, 0, 0, "", (ip, 443))]
        )
        with pytest.raises(Exception, match="public internet"):
            webhooks.check_url("https://hooks.example.com/x")
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 443))]
    )
    webhooks.check_url("https://hooks.example.com/x")


class Receiver:
    """A real HTTP server on localhost that records each delivery (the 'test client')."""

    def __init__(self, status=200):
        received = self.received = []
        reply = status

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.headers, body))  # case-insensitive, like any server
                self.send_response(reply)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hooks"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def receiver():
    server = Receiver()
    yield server
    server.close()


def test_a_test_client_receives_signed_webhooks_and_verifies_them(
    portal, client_tenant, gas_template, receiver, django_capture_on_commit_callbacks
):
    api = portal("owner")
    endpoint = api.post(
        HOOKS,
        {"url": receiver.url, "events": ["booking.created", "booking.date_set"]},
        format="json",
    ).json()
    assert endpoint["secret"].startswith("whsec_")
    with django_capture_on_commit_callbacks(execute=True):
        api.post("/api/portal/subscribers", SUBSCRIBER, format="json")
        api.post(
            "/api/portal/bookings",
            {
                "subscriber_ref": "KL0991",
                "booking_reference": "BK-HOOK-1",
                "booking_date": str(TODAY),
                "scheduled_date": str(TODAY + dt.timedelta(days=1)),
                "custom_fields": {"cylinder_type": "14.2 kg domestic", "quantity": 1},
            },
            format="json",
        )
    assert [h["X-SC-Event"] for h, _b in receiver.received] == [
        "booking.created",
        "booking.date_set",
    ]
    for headers, body in receiver.received:
        assert receiver_verify(endpoint["secret"], body, headers["X-SC-Signature"])
        assert not receiver_verify("whsec_wrong", body, headers["X-SC-Signature"])
        assert webhooks.verify(endpoint["secret"], body, headers["X-SC-Signature"])
    payload = json.loads(receiver.received[0][1])
    assert payload["type"] == "booking.created" and payload["tenant_id"] == str(client_tenant.pk)
    assert (
        payload["data"]["booking_reference"] == "BK-HOOK-1"
        and payload["data"]["subscriber_ref"] == "KL0991"
    )
    assert receiver.received[0][0]["X-SC-Delivery"] == payload["id"]
    log = api.get(DELIVERIES).json()["results"]
    assert {d["status"] for d in log} == {"succeeded"} and log[0]["last_status_code"] == 200


def test_test_event_and_signature_tolerance(portal, receiver, django_capture_on_commit_callbacks):
    api = portal("owner")
    endpoint = api.post(
        HOOKS, {"url": receiver.url, "events": ["outcome.recorded"]}, format="json"
    ).json()
    with django_capture_on_commit_callbacks(execute=True):
        delivery = api.post(f"{HOOKS}/{endpoint['id']}/test").json()
    assert delivery["event_type"] == "test"
    headers, body = receiver.received[0]
    assert headers["X-SC-Event"] == "test"
    old = int(timezone.now().timestamp()) - 600
    stale = webhooks.sign(endpoint["secret"], body, old)
    assert not webhooks.verify(endpoint["secret"], body, stale)  # replayed after 10 minutes
    assert not webhooks.verify(endpoint["secret"], body, "garbage")

    rotated = api.post(f"{HOOKS}/{endpoint['id']}/rotate-secret").json()
    assert rotated["secret"] != endpoint["secret"]


def test_failures_retry_with_back_off_for_24_hours(
    portal, client_tenant, monkeypatch, django_capture_on_commit_callbacks
):
    api = portal("owner")
    endpoint = api.post(
        HOOKS,
        {"url": "https://hooks.example.com/x", "events": ["booking.cancelled"]},
        format="json",
    ).json()
    calls = []

    def failing(request, timeout=None):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 500, "Server Error", {}, io.BytesIO())

    monkeypatch.setattr("urllib.request.urlopen", failing)
    with tenant_context(client_tenant):
        [delivery] = webhooks.emit(
            client_tenant.pk, "booking.cancelled", {"booking_reference": "BK1"}
        )
    with tenant_context(client_tenant):
        assert webhooks.attempt(delivery.pk) == "pending"
        delivery.refresh_from_db()
    assert (delivery.attempts, delivery.last_status_code) == (1, 500)
    assert abs((delivery.next_attempt_at - timezone.now()).total_seconds() - 60) < 5
    # 23 h 59 m later, the next wait would pass the 24-hour window: it fails for good.
    with tenant_context(client_tenant):
        WebhookDelivery.objects.filter(pk=delivery.pk).update(
            created_at=timezone.now() - dt.timedelta(hours=23, minutes=59)
        )
        assert webhooks.attempt(delivery.pk) == "failed"
    log = api.get(DELIVERIES, {"status": "failed"}).json()["results"]
    assert log[0]["attempts"] == 2 and log[0]["endpoint_url"] == endpoint["url"]

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: io.BytesIO(b""))
    retried = api.post(f"{DELIVERIES}/{delivery.pk}/retry").json()
    assert retried["status"] == "pending"


def test_unsubscribed_or_inactive_endpoints_get_nothing(portal, client_tenant):
    api = portal("owner")
    first = api.post(
        HOOKS, {"url": "https://a.example.com", "events": ["booking.created"]}, format="json"
    ).json()
    api.post(HOOKS, {"url": "https://b.example.com", "events": ["outcome.recorded"]}, format="json")
    with tenant_context(client_tenant):
        assert len(webhooks.emit(client_tenant.pk, "outcome.recorded", {})) == 1
    api.patch(f"{HOOKS}/{first['id']}", {"is_active": False}, format="json")
    with tenant_context(client_tenant):
        assert webhooks.emit(client_tenant.pk, "booking.created", {}) == []
