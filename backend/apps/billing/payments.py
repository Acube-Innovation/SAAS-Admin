"""
Payment providers (portal spec 2.9): Razorpay (primary), Cashfree (fallback) and a local stub.

Every provider turns our checkout and mandate requests into provider API calls and turns its
signed webhooks into a common `WebhookEvent`. Calls use plain HTTPS from the standard library.
Our own references (tenant, order, mandate, charge) travel in the provider's notes / tags, so a
verified webhook names its tenant without any cross-tenant lookup.

The Razorpay and Cashfree request and payload shapes follow their public API documentation and
have not yet been exercised against a live sandbox (credentials pending): see the spec, 2.9.
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from apps.core.exceptions import ApiError

TIMEOUT_SECONDS = 15
# The mandate is authorised for the largest auto top-up (₹15,000) plus 18% GST.
MANDATE_MAX_TOTAL_PAISE = 1_770_000


class ProviderUnavailable(Exception):
    """The provider isn't configured or didn't answer; the next provider may be tried."""


class ProviderError(Exception):
    """The provider refused the request."""


@dataclass
class Checkout:
    """What the browser needs to open the provider's payment page."""

    provider: str
    data: dict


@dataclass
class WebhookEvent:
    event_id: str
    kind: str  # payment_succeeded, payment_failed, mandate_activated, mandate_paused,
    # mandate_resumed, mandate_cancelled, mandate_failed, ignored
    notes: dict = field(default_factory=dict)  # our references: tenant, order, mandate, charge
    provider_payment_id: str = ""
    provider_order_id: str = ""
    provider_mandate_id: str = ""
    provider_customer_id: str = ""
    amount_paise: int | None = None
    reason: str = ""
    raw_type: str = ""


def _http(method, url, *, body=None, headers=None, auth=None):
    if not url.startswith("https://"):
        raise ProviderUnavailable(f"Refusing a non-HTTPS provider URL: {url}")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 (https only)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            text = response.read().decode() or "{}"
            return json.loads(text)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        if error.code >= 500:
            raise ProviderUnavailable(f"{error.code}: {detail}") from error
        raise ProviderError(f"{error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProviderUnavailable(str(error)) from error


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class PaymentProvider(ABC):
    name: str

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def create_order(self, order, tenant) -> Checkout: ...

    @abstractmethod
    def create_mandate(self, mandate, tenant) -> Checkout: ...

    @abstractmethod
    def charge_mandate(self, mandate, tenant, total_paise: int, charge_ref: str) -> str:
        """Start a debit on the mandate and return the provider's reference (result: webhook)."""

    @abstractmethod
    def cancel_mandate(self, mandate) -> None: ...

    @abstractmethod
    def verify(self, body: bytes, headers) -> bool: ...

    @abstractmethod
    def parse(self, body: bytes, headers) -> WebhookEvent: ...

    def require_configured(self):
        if not self.configured():
            raise ProviderUnavailable(f"{self.name} is not configured")


def _customer(tenant):
    return {
        "name": tenant.legal_name[:50],
        "email": tenant.contact_email,
        "contact": tenant.contact_phone,
    }


# --- Razorpay ---------------------------------------------------------------------------------


class RazorpayProvider(PaymentProvider):
    """Orders + Checkout for top-ups; recurring tokens (UPI AutoPay, cards) for auto top-up."""

    name = "razorpay"

    def configured(self):
        return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)

    def _call(self, method, path, body=None):
        self.require_configured()
        return _http(
            method,
            f"{settings.RAZORPAY_API_BASE}{path}",
            body=body,
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
        )

    def create_order(self, order, tenant):
        created = self._call(
            "POST",
            "/orders",
            {
                "amount": order.total_paise,
                "currency": "INR",
                "receipt": str(order.pk)[:40],
                "notes": {"tenant": str(tenant.pk), "order": str(order.pk)},
            },
        )
        order.provider_order_id = created["id"]
        return Checkout(
            self.name,
            {
                "key_id": settings.RAZORPAY_KEY_ID,
                "order_id": created["id"],
                "amount": order.total_paise,
                "currency": "INR",
                "name": settings.SELLER_LEGAL_NAME,
                "prefill": _customer(tenant),
            },
        )

    def create_mandate(self, mandate, tenant):
        customer = self._call(
            "POST",
            "/customers",
            {**_customer(tenant), "fail_existing": "0", "notes": {"tenant": str(tenant.pk)}},
        )
        mandate.provider_customer_id = customer["id"]
        notes = {"tenant": str(tenant.pk), "mandate": str(mandate.pk)}
        expire_at = int((timezone.now() + dt.timedelta(days=3650)).timestamp())
        order = self._call(
            "POST",
            "/orders",
            {
                "amount": 100,  # ₹1 authorisation payment
                "currency": "INR",
                "method": mandate.method,
                "customer_id": customer["id"],
                "token": {
                    "max_amount": MANDATE_MAX_TOTAL_PAISE,
                    "expire_at": expire_at,
                    "frequency": "as_presented",
                },
                "notes": notes,
            },
        )
        return Checkout(
            self.name,
            {
                "key_id": settings.RAZORPAY_KEY_ID,
                "order_id": order["id"],
                "customer_id": customer["id"],
                "recurring": "1",
                "amount": 100,
                "currency": "INR",
                "name": settings.SELLER_LEGAL_NAME,
                "prefill": _customer(tenant),
            },
        )

    def charge_mandate(self, mandate, tenant, total_paise, charge_ref):
        notes = {"tenant": str(tenant.pk), "mandate": str(mandate.pk), "charge": charge_ref}
        order = self._call(
            "POST",
            "/orders",
            {"amount": total_paise, "currency": "INR", "payment_capture": True, "notes": notes},
        )
        payment = self._call(
            "POST",
            "/payments/create/recurring",
            {
                **{k: v for k, v in _customer(tenant).items() if k != "name"},
                "amount": total_paise,
                "currency": "INR",
                "order_id": order["id"],
                "customer_id": mandate.provider_customer_id,
                "token": mandate.provider_mandate_id,
                "recurring": "1",
                "notes": notes,
            },
        )
        return payment.get("razorpay_payment_id") or order["id"]

    def cancel_mandate(self, mandate):
        if mandate.provider_customer_id and mandate.provider_mandate_id:
            self._call(
                "DELETE",
                f"/customers/{mandate.provider_customer_id}/tokens/{mandate.provider_mandate_id}",
            )

    def verify(self, body, headers):
        secret = settings.RAZORPAY_WEBHOOK_SECRET
        signature = headers.get("X-Razorpay-Signature", "")
        return bool(secret and signature) and hmac.compare_digest(
            _hmac_hex(secret, body), signature
        )

    def parse(self, body, headers):
        data = json.loads(body)
        event_type = data.get("event", "")
        payload = data.get("payload", {})
        payment = payload.get("payment", {}).get("entity", {})
        token = payload.get("token", {}).get("entity", {})
        notes = payment.get("notes") or token.get("notes") or {}
        if isinstance(notes, list):  # Razorpay sends [] for "no notes"
            notes = {}
        event = WebhookEvent(
            event_id=headers.get("X-Razorpay-Event-Id") or f"{event_type}:{payment.get('id')}",
            kind="ignored",
            notes=notes,
            provider_payment_id=payment.get("id", ""),
            provider_order_id=payment.get("order_id", ""),
            provider_customer_id=payment.get("customer_id") or token.get("customer_id", ""),
            provider_mandate_id=payment.get("token_id") or token.get("id", ""),
            amount_paise=payment.get("amount"),
            reason=payment.get("error_description") or "",
            raw_type=event_type,
        )
        recurring_auth = "mandate" in notes and "charge" not in notes
        if event_type == "payment.captured":
            event.kind = "mandate_activated" if recurring_auth else "payment_succeeded"
        elif event_type == "payment.failed":
            event.kind = "mandate_failed" if recurring_auth else "payment_failed"
        elif event_type == "token.paused":
            event.kind = "mandate_paused"
        elif event_type == "token.cancelled":
            event.kind = "mandate_cancelled"
        return event


# --- Cashfree ---------------------------------------------------------------------------------


class CashfreeProvider(PaymentProvider):
    """PG orders for top-ups; Subscriptions (on-demand) for auto top-up."""

    name = "cashfree"

    def configured(self):
        return bool(settings.CASHFREE_APP_ID and settings.CASHFREE_SECRET_KEY)

    def _call(self, method, path, body=None):
        self.require_configured()
        return _http(
            method,
            f"{settings.CASHFREE_API_BASE}{path}",
            body=body,
            headers={
                "x-client-id": settings.CASHFREE_APP_ID,
                "x-client-secret": settings.CASHFREE_SECRET_KEY,
                "x-api-version": settings.CASHFREE_API_VERSION,
            },
        )

    @staticmethod
    def _rupees(paise):
        return round(paise / 100, 2)

    def _customer_details(self, tenant):
        return {
            "customer_id": tenant.pk.hex[:50],
            "customer_name": tenant.legal_name[:50],
            "customer_email": tenant.contact_email,
            "customer_phone": tenant.contact_phone.removeprefix("+91")[-10:],
        }

    def create_order(self, order, tenant):
        created = self._call(
            "POST",
            "/orders",
            {
                "order_id": f"sc_{order.pk.hex}",
                "order_amount": self._rupees(order.total_paise),
                "order_currency": "INR",
                "customer_details": self._customer_details(tenant),
                "order_tags": {"tenant": str(tenant.pk), "order": str(order.pk)},
            },
        )
        order.provider_order_id = created["order_id"]
        return Checkout(
            self.name,
            {
                "payment_session_id": created["payment_session_id"],
                "order_id": created["order_id"],
                "mode": settings.CASHFREE_MODE,
            },
        )

    def create_mandate(self, mandate, tenant):
        subscription_id = f"sc_{mandate.pk.hex}"
        created = self._call(
            "POST",
            "/subscriptions",
            {
                "subscription_id": subscription_id,
                "customer_details": self._customer_details(tenant),
                "plan_details": {
                    "plan_name": "Auto top-up",
                    "plan_type": "ON_DEMAND",
                    "plan_max_amount": self._rupees(MANDATE_MAX_TOTAL_PAISE),
                },
                "authorization_details": {
                    "authorization_amount": 1,
                    "payment_methods": [mandate.method],
                },
                "subscription_tags": {"tenant": str(tenant.pk), "mandate": str(mandate.pk)},
            },
        )
        mandate.provider_mandate_id = subscription_id
        return Checkout(
            self.name,
            {
                "subscription_session_id": created.get("subscription_session_id", ""),
                "subscription_id": subscription_id,
                "mode": settings.CASHFREE_MODE,
            },
        )

    def charge_mandate(self, mandate, tenant, total_paise, charge_ref):
        self._call(
            "POST",
            "/subscriptions/pay",
            {
                "subscription_id": mandate.provider_mandate_id,
                "payment_id": charge_ref,
                "payment_amount": self._rupees(total_paise),
                "payment_type": "CHARGE",
            },
        )
        return charge_ref

    def cancel_mandate(self, mandate):
        if mandate.provider_mandate_id:
            self._call(
                "POST",
                f"/subscriptions/{mandate.provider_mandate_id}/manage",
                {"subscription_id": mandate.provider_mandate_id, "action": "CANCEL"},
            )

    def verify(self, body, headers):
        secret = settings.CASHFREE_SECRET_KEY
        signature = headers.get("x-webhook-signature", "")
        timestamp = headers.get("x-webhook-timestamp", "")
        if not (secret and signature and timestamp):
            return False
        expected = base64.b64encode(
            hmac.new(secret.encode(), timestamp.encode() + body, hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(expected, signature)

    def parse(self, body, headers):
        data = json.loads(body)
        event_type = data.get("type", "")
        payload = data.get("data", {})
        order = payload.get("order", {})
        payment = payload.get("payment", {})
        subscription = payload.get("subscription_details", {})
        notes = order.get("order_tags") or payload.get("subscription_tags") or {}
        amount = payment.get("payment_amount", payload.get("payment_amount"))
        payment_id = str(payment.get("cf_payment_id") or payload.get("cf_payment_id") or "")
        event = WebhookEvent(
            event_id=headers.get("x-webhook-id") or f"{event_type}:{payment_id or subscription}",
            kind="ignored",
            notes=dict(notes),
            provider_payment_id=payment_id,
            provider_order_id=order.get("order_id", ""),
            provider_mandate_id=subscription.get("subscription_id", ""),
            amount_paise=round(float(amount) * 100) if amount is not None else None,
            reason=payment.get("payment_message")
            or payload.get("failure_details", {}).get("failure_reason", ""),
            raw_type=event_type,
        )
        if payload.get("payment_id"):  # our charge reference on subscription payments
            event.notes.setdefault("charge", payload["payment_id"])
        status = subscription.get("subscription_status", "")
        if event_type == "PAYMENT_SUCCESS_WEBHOOK":
            event.kind = "payment_succeeded"
        elif event_type in ("PAYMENT_FAILED_WEBHOOK", "PAYMENT_USER_DROPPED_WEBHOOK"):
            event.kind = "payment_failed"
        elif event_type == "SUBSCRIPTION_PAYMENT_SUCCESS":
            event.kind = "payment_succeeded"
        elif event_type in ("SUBSCRIPTION_PAYMENT_FAILED", "SUBSCRIPTION_PAYMENT_CANCELLED"):
            event.kind = "payment_failed"
        elif event_type == "SUBSCRIPTION_STATUS_CHANGED":
            event.kind = {
                "ACTIVE": "mandate_activated",
                "ON_HOLD": "mandate_paused",
                "CANCELLED": "mandate_cancelled",
                "CUSTOMER_CANCELLED": "mandate_cancelled",
                "EXPIRED": "mandate_cancelled",
                "BANK_APPROVAL_PENDING": "ignored",
            }.get(status, "ignored")
        return event


# --- Stub (local development and tests) -------------------------------------------------------


class StubProvider(PaymentProvider):
    """
    A local stand-in: no network calls. The DEBUG-only simulate endpoint (and tests) send the
    signed webhooks a provider would. Never available when DEBUG is off.
    """

    name = "stub"
    # A fixed, public value: the stub only works with DEBUG on or in tests (see configured()).
    SECRET = "stub-webhook-secret-local-only"  # noqa: S105
    charges: list = []  # recorded charge and cancel calls, for tests

    def configured(self):
        return bool(settings.DEBUG and settings.PAYMENT_STUB_ENABLED) or settings.TESTING

    def create_order(self, order, tenant):
        self.require_configured()
        order.provider_order_id = f"stub_order_{uuid.uuid4().hex[:16]}"
        return Checkout(self.name, {"order_id": order.provider_order_id})

    def create_mandate(self, mandate, tenant):
        self.require_configured()
        mandate.provider_mandate_id = f"stub_mandate_{uuid.uuid4().hex[:16]}"
        mandate.provider_customer_id = f"stub_customer_{tenant.pk.hex[:12]}"
        return Checkout(self.name, {"mandate_id": mandate.provider_mandate_id})

    def charge_mandate(self, mandate, tenant, total_paise, charge_ref):
        self.require_configured()
        StubProvider.charges.append(("charge", str(mandate.pk), total_paise, charge_ref))
        return f"stub_pay_{charge_ref}"

    def cancel_mandate(self, mandate):
        StubProvider.charges.append(("cancel", str(mandate.pk)))

    def verify(self, body, headers):
        return self.configured() and hmac.compare_digest(
            _hmac_hex(self.SECRET, body), headers.get("X-Stub-Signature", "")
        )

    def parse(self, body, headers):
        data = json.loads(body)
        return WebhookEvent(
            event_id=data["id"],
            kind=data["kind"],
            notes=data.get("notes", {}),
            provider_payment_id=data.get("payment_id", ""),
            provider_order_id=data.get("order_id", ""),
            provider_mandate_id=data.get("mandate_id", ""),
            amount_paise=data.get("amount"),
            reason=data.get("reason", ""),
            raw_type=data["kind"],
        )

    @classmethod
    def signed(cls, event: dict) -> tuple[bytes, dict]:
        """A webhook body and headers as the stub 'provider' would send them."""
        body = json.dumps(event).encode()
        return body, {"X-Stub-Signature": _hmac_hex(cls.SECRET, body)}


PROVIDERS = {p.name: p for p in (RazorpayProvider(), CashfreeProvider(), StubProvider())}


def get_provider(name: str) -> PaymentProvider:
    try:
        return PROVIDERS[name]
    except KeyError as error:
        raise ApiError("unknown_provider", f"Unknown payment provider {name!r}.", 404) from error


def providers_in_order() -> list[PaymentProvider]:
    """Configured providers, primary first (PAYMENT_PROVIDERS), then the stub when allowed."""
    ordered = [PROVIDERS[n] for n in settings.PAYMENT_PROVIDERS if n in PROVIDERS]
    available = [p for p in ordered if p.configured()]
    stub = PROVIDERS["stub"]
    if stub.configured() and stub not in available:
        available.append(stub)
    return available
