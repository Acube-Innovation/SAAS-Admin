"""Payments (portal spec 2.9): top-up, auto top-up mandates, webhooks, providers, plan view."""

import base64
import datetime as dt
import hashlib
import hmac
import io
import json
import urllib.error
import uuid

import pytest
from django.core import mail
from django.utils import timezone

from apps.billing import payment_services as payments
from apps.billing import services as billing
from apps.billing.models import Invoice, PaymentMandate, PaymentOrder, WalletTransaction
from apps.billing.payments import CashfreeProvider, RazorpayProvider, StubProvider
from apps.change_requests.models import ChangeRequest
from apps.core.tenancy import tenant_context
from tests import factories

pytestmark = pytest.mark.django_db
WEBHOOK = "/api/payments/webhooks"
SIMULATE = "/api/portal/dev/payments/simulate"


def balance(tenant):
    with tenant_context(tenant):
        return billing.wallet_for(tenant).balance_paise


def credits(tenant):
    with tenant_context(tenant):
        return list(WalletTransaction.objects.filter(tenant=tenant, kind="credit"))


def reload(obj):
    with tenant_context(obj.tenant_id):
        return type(obj).objects.get(pk=obj.pk)


@pytest.fixture(autouse=True)
def no_real_providers(settings):
    settings.RAZORPAY_KEY_ID = settings.RAZORPAY_KEY_SECRET = settings.RAZORPAY_WEBHOOK_SECRET = ""
    settings.CASHFREE_APP_ID = settings.CASHFREE_SECRET_KEY = ""
    StubProvider.charges.clear()


@pytest.fixture
def razorpay(settings):
    settings.RAZORPAY_KEY_ID = "rzp_test_key"
    settings.RAZORPAY_KEY_SECRET = "rzp_test_secret"
    settings.RAZORPAY_WEBHOOK_SECRET = "rzp_webhook_secret"


@pytest.fixture
def cashfree(settings):
    settings.CASHFREE_APP_ID = "cf_app"
    settings.CASHFREE_SECRET_KEY = "cf_secret"


class FakeHttp:
    """Stands in for urllib.request.urlopen: records requests, answers from a script."""

    def __init__(self, monkeypatch, answers):
        self.requests = []
        self.answers = list(answers)
        monkeypatch.setattr("urllib.request.urlopen", self)

    def __call__(self, request, timeout=None):
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), request.full_url, dict(request.headers), body))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


def stub_webhook(api, event):
    body, headers = StubProvider.signed(event)
    return api.post(
        f"{WEBHOOK}/stub",
        body,
        content_type="application/json",
        HTTP_X_STUB_SIGNATURE=headers["X-Stub-Signature"],
    )


# --- providers --------------------------------------------------------------------------------


def test_razorpay_signature(razorpay):
    body = b'{"event":"payment.captured"}'
    good = hmac.new(b"rzp_webhook_secret", body, hashlib.sha256).hexdigest()
    provider = RazorpayProvider()
    assert provider.verify(body, {"X-Razorpay-Signature": good})
    assert not provider.verify(body + b" ", {"X-Razorpay-Signature": good})
    assert not provider.verify(body, {})


def test_cashfree_signature(cashfree):
    body, timestamp = b'{"type":"PAYMENT_SUCCESS_WEBHOOK"}', "1761700000000"
    good = base64.b64encode(
        hmac.new(b"cf_secret", timestamp.encode() + body, hashlib.sha256).digest()
    ).decode()
    provider = CashfreeProvider()
    headers = {"x-webhook-signature": good, "x-webhook-timestamp": timestamp}
    assert provider.verify(body, headers)
    assert not provider.verify(body, {**headers, "x-webhook-timestamp": "1"})


def test_razorpay_is_primary_and_gets_the_order_with_gst(
    client_tenant, owner, razorpay, monkeypatch
):
    http = FakeHttp(monkeypatch, [{"id": "order_Rzp123"}])
    order, checkout = payments.start_top_up(client_tenant, 100_000, owner)
    method, url, headers, body = http.requests[0]
    assert (method, url) == ("POST", "https://api.razorpay.com/v1/orders")
    assert (
        headers["Authorization"]
        == "Basic " + base64.b64encode(b"rzp_test_key:rzp_test_secret").decode()
    )
    assert body["amount"] == 118_000 and body["currency"] == "INR"
    assert body["notes"] == {"tenant": str(client_tenant.pk), "order": str(order.pk)}
    assert (order.provider, order.provider_order_id) == ("razorpay", "order_Rzp123")
    assert (order.credit_paise, order.gst_paise, order.total_paise) == (100_000, 18_000, 118_000)
    assert checkout.provider == "razorpay" and checkout.data["key_id"] == "rzp_test_key"
    assert "rzp_test_secret" not in json.dumps(checkout.data)


def test_cashfree_takes_over_when_razorpay_is_down(
    client_tenant, owner, razorpay, cashfree, monkeypatch
):
    http = FakeHttp(
        monkeypatch,
        [
            urllib.error.URLError("connection refused"),
            {"order_id": "sc_x", "payment_session_id": "session_abc"},
        ],
    )
    order, checkout = payments.start_top_up(client_tenant, 250_000, owner)
    assert [r[1] for r in http.requests] == [
        "https://api.razorpay.com/v1/orders",
        "https://sandbox.cashfree.com/pg/orders",
    ]
    _m, _u, headers, body = http.requests[1]
    assert headers["X-client-id"] == "cf_app" and headers["X-api-version"] == "2023-08-01"
    assert body["order_amount"] == 2950.0 and body["order_tags"]["order"] == str(order.pk)
    assert order.provider == "cashfree" and checkout.data["payment_session_id"] == "session_abc"


def test_the_stub_only_runs_in_development_or_tests(client_tenant, owner, settings):
    assert payments.start_top_up(client_tenant, 100_000, owner)[0].provider == "stub"
    settings.TESTING = False
    settings.DEBUG = False
    with pytest.raises(Exception, match="unavailable"):
        payments.start_top_up(client_tenant, 100_000, owner)


@pytest.mark.parametrize("credit", [99_900, 100_050, 50_000_100, 0])
def test_top_up_amount_rules(client_tenant, owner, credit):
    with pytest.raises(Exception, match="whole rupees"):
        payments.start_top_up(client_tenant, credit, owner)


# --- manual top-up via the API ----------------------------------------------------------------


def test_top_up_is_credited_once_by_the_signed_webhook(portal, client_tenant, api):
    owner_api = portal("owner")
    started = owner_api.post("/api/portal/top-ups", {"credit_paise": 100_000}, format="json")
    assert started.status_code == 201, started.json()
    order = started.json()["order"]
    assert (order["total_paise"], order["status"], started.json()["checkout"]["provider"]) == (
        118_000,
        "created",
        "stub",
    )
    assert balance(client_tenant) == 0  # nothing until the webhook
    event = {
        "id": "evt_1",
        "kind": "payment_succeeded",
        "notes": {"tenant": str(client_tenant.pk), "order": order["id"]},
        "payment_id": "stub_pay_1",
        "amount": 118_000,
    }
    from rest_framework.test import APIClient

    public = APIClient()  # webhooks come with no session
    assert stub_webhook(public, event).json()["status"] == "credited"
    assert stub_webhook(public, event).json()["status"] == "duplicate"
    resent = {**event, "id": "evt_1_retry"}  # same payment, new event id
    assert stub_webhook(public, resent).json()["status"] == "already_paid"
    assert balance(client_tenant) == 100_000 and len(credits(client_tenant)) == 1
    with tenant_context(client_tenant):
        invoice = Invoice.objects.get()
    assert invoice.total_paise == 118_000 and invoice.payment_reference == "stub:stub_pay_1"
    status = owner_api.get(f"/api/portal/top-ups/{order['id']}").json()
    assert status["status"] == "paid" and status["paid_at"]


def test_bad_or_foreign_webhooks_change_nothing(portal, client_tenant):
    from rest_framework.test import APIClient

    order = portal("owner").post("/api/portal/top-ups", {"credit_paise": 100_000}).json()["order"]
    public = APIClient()
    event = {
        "id": "evt_x",
        "kind": "payment_succeeded",
        "notes": {"tenant": str(client_tenant.pk), "order": order["id"]},
        "amount": 118_000,
    }
    body, _headers = StubProvider.signed(event)
    response = public.post(
        f"{WEBHOOK}/stub", body, content_type="application/json", HTTP_X_STUB_SIGNATURE="forged"
    )
    assert response.status_code == 400 and response.json()["code"] == "invalid_signature"
    assert stub_webhook(public, {**event, "id": "evt_y", "notes": {}}).json()["status"] == (
        "ignored_no_tenant"
    )
    other = factories.make_tenant("Other Agency")
    foreign = {**event, "id": "evt_z", "notes": {"tenant": str(other.pk), "order": order["id"]}}
    assert stub_webhook(public, foreign).json()["status"] == "unknown_order"
    assert (
        public.post(f"{WEBHOOK}/paypal", body, content_type="application/json").status_code == 404
    )
    assert balance(client_tenant) == 0 and balance(other) == 0


def test_a_short_payment_is_not_credited(portal, client_tenant):
    from rest_framework.test import APIClient

    order = portal("owner").post("/api/portal/top-ups", {"credit_paise": 100_000}).json()["order"]
    event = {
        "id": "evt_short",
        "kind": "payment_succeeded",
        "notes": {"tenant": str(client_tenant.pk), "order": order["id"]},
        "amount": 100_000,  # GST missing
    }
    assert stub_webhook(APIClient(), event).json()["status"] == "amount_mismatch"
    assert balance(client_tenant) == 0
    with tenant_context(client_tenant):
        assert PaymentOrder.objects.get().status == "amount_mismatch"


def test_razorpay_webhook_credits_once(client_tenant, owner, razorpay, monkeypatch, api):
    FakeHttp(monkeypatch, [{"id": "order_Rzp9"}])
    order, _checkout = payments.start_top_up(client_tenant, 200_000, owner)
    body = json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_Rzp9",
                        "order_id": "order_Rzp9",
                        "amount": 236_000,
                        "notes": {"tenant": str(client_tenant.pk), "order": str(order.pk)},
                    }
                }
            },
        }
    ).encode()
    signature = hmac.new(b"rzp_webhook_secret", body, hashlib.sha256).hexdigest()
    headers = {"HTTP_X_RAZORPAY_SIGNATURE": signature, "HTTP_X_RAZORPAY_EVENT_ID": "evt_rzp_1"}
    first = api.post(f"{WEBHOOK}/razorpay", body, content_type="application/json", **headers)
    again = api.post(f"{WEBHOOK}/razorpay", body, content_type="application/json", **headers)
    assert (first.json()["status"], again.json()["status"]) == ("credited", "duplicate")
    assert balance(client_tenant) == 200_000
    assert credits(client_tenant)[0].idempotency_key == "payment:razorpay:pay_Rzp9"


def test_cashfree_webhook_credits(client_tenant, owner, cashfree, monkeypatch, api):
    FakeHttp(monkeypatch, [{"order_id": "sc_1", "payment_session_id": "s"}])
    order, _checkout = payments.start_top_up(client_tenant, 100_000, owner)
    body = json.dumps(
        {
            "type": "PAYMENT_SUCCESS_WEBHOOK",
            "data": {
                "order": {
                    "order_id": "sc_1",
                    "order_tags": {"tenant": str(client_tenant.pk), "order": str(order.pk)},
                },
                "payment": {"cf_payment_id": 555, "payment_amount": 1180.0},
            },
        }
    ).encode()
    timestamp = "1761700000000"
    signature = base64.b64encode(
        hmac.new(b"cf_secret", timestamp.encode() + body, hashlib.sha256).digest()
    ).decode()
    headers = {"HTTP_X_WEBHOOK_SIGNATURE": signature, "HTTP_X_WEBHOOK_TIMESTAMP": timestamp}
    for expected in ("credited", "duplicate"):
        response = api.post(f"{WEBHOOK}/cashfree", body, content_type="application/json", **headers)
        assert response.json()["status"] == expected
    assert balance(client_tenant) == 100_000


def test_failed_payment_marks_the_order(portal, client_tenant):
    api = portal("owner")
    order = api.post("/api/portal/top-ups", {"credit_paise": 100_000}).json()["order"]
    outcome = api.post(
        SIMULATE, {"target": "order", "id": order["id"], "outcome": "failure"}
    ).json()
    assert outcome["outcomes"] == ["order_failed"]
    assert api.get(f"/api/portal/top-ups/{order['id']}").json()["status"] == "failed"


@pytest.mark.parametrize("role", ["manager", "office_staff"])
def test_only_the_owner_pays(portal, role):
    api = portal(role)
    assert api.post("/api/portal/top-ups", {"credit_paise": 100_000}).status_code == 403
    assert (
        api.post("/api/portal/auto-top-up", {"credit_paise": 100_000, "method": "upi"}).status_code
        == 403
    )
    expected = 200 if role == "manager" else 403
    assert api.get("/api/portal/auto-top-up").status_code == expected
    assert api.get("/api/portal/plan").status_code == expected


# --- auto top-up ------------------------------------------------------------------------------


def set_up_mandate(api, credit=200_000):
    started = api.post("/api/portal/auto-top-up", {"credit_paise": credit, "method": "upi"}).json()
    mandate_id = started["mandate"]["id"]
    assert started["mandate"]["status"] == "pending"
    assert api.post(SIMULATE, {"target": "mandate", "id": mandate_id}).json()["outcomes"] == [
        "mandate_active"
    ]
    return mandate_id


def test_mandate_debit_credits_once_when_the_balance_runs_low(
    portal, client_tenant, django_capture_on_commit_callbacks
):
    api = portal("owner")
    billing.credit(client_tenant, 101_000, reason="adjustment")  # above the ₹1,000 level
    mandate_id = set_up_mandate(api)
    with tenant_context(client_tenant):
        mandate = PaymentMandate.objects.get(pk=mandate_id)
    assert mandate.status == "active" and mandate.charge_in_flight == ""
    with django_capture_on_commit_callbacks(execute=True):
        billing.debit(client_tenant, 85)
        billing.debit(client_tenant, 1000)  # crosses the ₹1,000 level -> auto top-up
    assert [c[:3] for c in StubProvider.charges] == [("charge", mandate_id, 236_000)]
    state = api.get("/api/portal/auto-top-up").json()
    assert state["mandate"]["charge_in_progress"] is True
    outcomes = api.post(SIMULATE, {"target": "charge", "id": mandate_id, "times": 2}).json()
    assert outcomes["outcomes"] == ["credited", "duplicate"]
    assert balance(client_tenant) == 101_000 - 1085 + 200_000
    auto = [c for c in credits(client_tenant) if c.reason == "auto_top_up"]
    assert len(auto) == 1 and auto[0].invoice_id
    state = api.get("/api/portal/auto-top-up").json()["mandate"]
    assert state["charge_in_progress"] is False and state["last_charged_at"]


def test_only_one_debit_at_a_time(portal, client_tenant):
    api = portal("owner")
    mandate_id = set_up_mandate(api)
    assert payments.trigger_auto_top_up(client_tenant) == "charge_started"  # empty wallet
    assert payments.trigger_auto_top_up(client_tenant) == "charge_in_flight"
    with tenant_context(client_tenant):
        PaymentMandate.objects.filter(pk=mandate_id).update(
            charge_started_at=timezone.now() - dt.timedelta(hours=7)
        )
    assert payments.trigger_auto_top_up(client_tenant) == "charge_started"  # the old one timed out
    assert len(StubProvider.charges) == 2


def test_failed_debit_is_recorded_and_can_retry(portal, client_tenant):
    api = portal("owner")
    mandate_id = set_up_mandate(api)
    payments.trigger_auto_top_up(client_tenant)
    outcome = api.post(
        SIMULATE, {"target": "charge", "id": mandate_id, "outcome": "failure"}
    ).json()
    assert outcome["outcomes"] == ["charge_failed"]
    state = api.get("/api/portal/auto-top-up").json()["mandate"]
    assert state["last_failure"] == "Insufficient funds (simulator)"
    assert state["charge_in_progress"] is False
    from apps.billing.tasks import check_auto_top_ups

    assert check_auto_top_ups() == 1  # the periodic check tries again
    assert balance(client_tenant) == 0


def test_pause_resume_change_and_cancel(portal, client_tenant, django_capture_on_commit_callbacks):
    api = portal("owner")
    mandate_id = set_up_mandate(api)
    assert api.post("/api/portal/auto-top-up/pause").json()["status"] == "paused"
    assert payments.trigger_auto_top_up(client_tenant) == "no_active_mandate"
    assert api.post("/api/portal/auto-top-up/pause").status_code == 409
    response = api.post("/api/portal/auto-top-up/amount", {"credit_paise": 1_600_000})
    assert response.status_code == 400
    assert (
        api.post("/api/portal/auto-top-up/amount", {"credit_paise": 500_000}).json()["credit_paise"]
        == 500_000
    )
    with django_capture_on_commit_callbacks(execute=True):
        assert api.post("/api/portal/auto-top-up/resume").json()["status"] == "active"
    assert StubProvider.charges[-1][:3] == ("charge", mandate_id, 590_000)  # low wallet, new amount
    second = api.post("/api/portal/auto-top-up", {"credit_paise": 100_000, "method": "card"})
    assert second.status_code == 409 and second.json()["code"] == "mandate_exists"
    assert api.post("/api/portal/auto-top-up/cancel").json()["status"] == "cancelled"
    assert ("cancel", mandate_id) in StubProvider.charges
    assert api.get("/api/portal/auto-top-up").json()["mandate"] is None
    assert api.post("/api/portal/auto-top-up/pause").status_code == 404
    again = api.post("/api/portal/auto-top-up", {"credit_paise": 100_000, "method": "card"})
    assert again.status_code == 201


@pytest.mark.parametrize("credit", [99_900, 1_500_100, 150_050])
def test_auto_top_up_amount_rules(portal, credit):
    response = portal("owner").post(
        "/api/portal/auto-top-up", {"credit_paise": credit, "method": "upi"}
    )
    assert response.status_code == 400


def test_razorpay_mandate_registration_and_recurring_debit(
    client_tenant, owner, razorpay, monkeypatch
):
    http = FakeHttp(monkeypatch, [{"id": "cust_1"}, {"id": "order_auth"}])
    mandate, checkout = payments.start_mandate(client_tenant, 150_000, "upi", owner)
    customer, order = http.requests[0][3], http.requests[1][3]
    assert customer["email"] == client_tenant.contact_email
    assert order["token"]["max_amount"] == 1_770_000 and order["method"] == "upi"
    assert order["notes"] == {"tenant": str(client_tenant.pk), "mandate": str(mandate.pk)}
    assert checkout.data["recurring"] == "1" and checkout.data["customer_id"] == "cust_1"
    with tenant_context(client_tenant):
        PaymentMandate.objects.filter(pk=mandate.pk).update(
            status="active", provider_mandate_id="token_1"
        )
    http.answers = [{"id": "order_debit"}, {"razorpay_payment_id": "pay_auto"}]
    assert payments.trigger_auto_top_up(client_tenant) == "charge_started"
    debit_order, recurring = http.requests[2][3], http.requests[3]
    assert debit_order["amount"] == 177_000
    assert (
        recurring[1].endswith("/payments/create/recurring") and recurring[3]["token"] == "token_1"
    )
    assert recurring[3]["notes"]["charge"] == reload(mandate).charge_in_flight


def test_simulator_is_not_available_outside_development(portal, settings):
    api = portal("owner")
    settings.TESTING = False
    assert api.post(SIMULATE, {"target": "order", "id": str(uuid.uuid4())}).status_code == 404


# --- plan -------------------------------------------------------------------------------------


def test_plan_view_and_change_request(
    portal, client_tenant, super_admin, django_capture_on_commit_callbacks
):
    api = portal("owner")
    data = api.get("/api/portal/plan").json()
    assert data["plan"]["code"] == "starter" and data["plan"]["is_placeholder"] is True
    assert [p["code"] for p in data["plans"]] == ["starter", "growth", "pro"]
    assert data["next_billing_date"] and data["active_subscribers"] == 0
    assert api.post("/api/portal/plan/request-change", {"plan_code": "starter"}).status_code == 400
    assert api.post("/api/portal/plan/request-change", {"plan_code": "gold"}).status_code == 400
    with django_capture_on_commit_callbacks(execute=True):
        response = api.post(
            "/api/portal/plan/request-change", {"plan_code": "growth", "note": "We need the API"}
        )
    assert response.status_code == 201
    assert response.json()["request_type"] == "plan_change"
    assert response.json()["subject"] == "Plan change to Growth (placeholder)"
    with tenant_context(client_tenant):
        assert ChangeRequest.objects.get().description == "We need the API"
    assert len(mail.outbox) == 1  # platform staff are told
