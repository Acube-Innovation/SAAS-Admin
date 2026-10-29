"""
Outgoing webhooks (portal spec 2.10). Each event becomes one WebhookDelivery per subscribed
endpoint, POSTed as JSON with:

    X-SC-Event:     the event type, e.g. outcome.recorded
    X-SC-Delivery:  the event id (the same on every retry, so receivers can ignore repeats)
    X-SC-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>" with the secret>

A 2xx answer is success; anything else (or no answer in 10 s) is retried with back-off until
24 hours after the event, then marked failed. Outside local development the URL must be HTTPS and
must not point at a private, loopback or link-local address.
"""

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.exceptions import ApiError
from apps.core.tenancy import tenant_context

from .models import EVENT_TYPES, WebhookDelivery, WebhookEndpoint

EVENTS = {code for code, _label in EVENT_TYPES}
TIMEOUT_SECONDS = 10
RETRY_WINDOW = dt.timedelta(hours=24)
# Seconds to wait after the 1st, 2nd, ... failed attempt.
BACKOFF = [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 43200]
MAX_ENDPOINTS = 5


def _allow_insecure() -> bool:
    return bool(settings.DEBUG or settings.TESTING)


def check_url(url: str, *, resolve: bool = True) -> None:
    """Refuse URLs we must not call: non-HTTPS, or private / internal addresses (SSRF)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ApiError("validation_error", "Enter a full URL starting with https://.", 400)
    if _allow_insecure():
        return
    if parsed.scheme != "https":
        raise ApiError("validation_error", "Webhook URLs must use HTTPS.", 400)
    if not resolve:
        return
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except socket.gaierror as error:
        raise ApiError("validation_error", "That host name doesn't resolve.", 400) from error
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ApiError("validation_error", "Webhook URLs must be on the public internet.", 400)


def sign(secret: str, body: bytes, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify(
    secret: str, body: bytes, header: str, tolerance: int = 300, now: int | None = None
) -> bool:
    """How a receiver checks a delivery (the same code is shown on the Integrations screen)."""
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        timestamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs((now or int(time.time())) - timestamp) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, body, timestamp), header)


def new_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"


# --- endpoints --------------------------------------------------------------------------------


def _clean_events(events):
    unknown = set(events) - EVENTS
    if unknown or not events:
        raise ApiError("validation_error", "Pick at least one event from the list.", 400)
    return sorted(set(events))


def create_endpoint(tenant, url, events, user) -> WebhookEndpoint:
    check_url(url)
    with tenant_context(tenant), transaction.atomic():
        if WebhookEndpoint.objects.filter(tenant=tenant, is_active=True).count() >= MAX_ENDPOINTS:
            raise ApiError("too_many_endpoints", f"Up to {MAX_ENDPOINTS} endpoints.", 409)
        endpoint = WebhookEndpoint.objects.create(
            tenant=tenant,
            url=url,
            events=_clean_events(events),
            secret=new_secret(),
            created_by=user,
        )
        write_audit(
            Action.CREATE, endpoint, after=snapshot(endpoint, exclude=("secret",)), actor=user
        )
        return endpoint


def update_endpoint(endpoint, data, user, *, rotate_secret=False) -> WebhookEndpoint:
    if "url" in data:
        check_url(data["url"])
    with tenant_context(endpoint.tenant_id), transaction.atomic():
        locked = WebhookEndpoint.objects.select_for_update().get(pk=endpoint.pk)
        before = snapshot(locked, exclude=("secret",))
        if "url" in data:
            locked.url = data["url"]
        if "events" in data:
            locked.events = _clean_events(data["events"])
        if "is_active" in data:
            locked.is_active = data["is_active"]
        if rotate_secret:
            locked.secret = new_secret()
        locked.save()
        after = {**snapshot(locked, exclude=("secret",)), "secret_rotated": rotate_secret}
        write_audit(Action.UPDATE, locked, before=before, after=after, actor=user)
        return locked


# --- events and deliveries --------------------------------------------------------------------


def envelope(tenant_id, event_type, data, event_id) -> dict:
    return {
        "id": str(event_id),
        "type": event_type,
        "created_at": timezone.now().isoformat(),
        "tenant_id": str(tenant_id),
        "data": data,
    }


def emit(tenant_id, event_type, data, *, endpoints=None) -> list[WebhookDelivery]:
    """Queue `event_type` for every active endpoint that subscribed to it (after commit)."""
    with tenant_context(tenant_id):
        targets = (
            endpoints
            if endpoints is not None
            else [
                e
                for e in WebhookEndpoint.objects.filter(tenant_id=tenant_id, is_active=True)
                if event_type in e.events
            ]
        )
        deliveries = []
        for endpoint in targets:
            delivery = WebhookDelivery(
                tenant_id=tenant_id, endpoint=endpoint, event_type=event_type
            )
            delivery.payload = json.loads(
                json.dumps(
                    envelope(tenant_id, event_type, data, delivery.event_id), cls=DjangoJSONEncoder
                )
            )
            delivery.next_attempt_at = timezone.now()
            delivery.save()
            deliveries.append(delivery)
    for delivery in deliveries:
        transaction.on_commit(lambda d=delivery: _queue(d))
    return deliveries


def _queue(delivery, countdown=0):
    from .tasks import deliver

    deliver.apply_async(
        kwargs={"tenant_id": delivery.tenant_id, "delivery_id": str(delivery.pk)},
        countdown=countdown,
    )


def _post(url, body: bytes, headers: dict) -> tuple[int | None, str]:
    request = urllib.request.Request(url, data=body, method="POST")  # noqa: S310 (checked URL)
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, ""
    except urllib.error.HTTPError as error:
        return error.code, f"HTTP {error.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, str(getattr(error, "reason", error))[:255]


def attempt(delivery_id) -> str:
    """One delivery attempt (the Celery task calls this). Returns the delivery's status."""
    with transaction.atomic():
        delivery = (
            WebhookDelivery.objects.select_for_update(of=("self",))
            .select_related("endpoint")
            .filter(pk=delivery_id)
            .first()
        )
        if delivery is None or delivery.status != WebhookDelivery.Status.PENDING:
            return delivery.status if delivery else "missing"
        endpoint = delivery.endpoint
        now = timezone.now()
        body = json.dumps(delivery.payload, separators=(",", ":")).encode()
        try:
            check_url(endpoint.url)
            code, error = _post(
                endpoint.url,
                body,
                {
                    "Content-Type": "application/json",
                    "User-Agent": "ScheduleConfirmation-Webhooks/1",
                    "X-SC-Event": delivery.event_type,
                    "X-SC-Delivery": str(delivery.event_id),
                    "X-SC-Signature": sign(endpoint.secret, body, int(now.timestamp())),
                },
            )
        except ApiError as refused:
            code, error = None, str(refused.detail)[:255]
        delivery.attempts += 1
        delivery.last_attempt_at = now
        delivery.last_status_code = code
        delivery.last_error = error
        if code is not None and 200 <= code < 300:
            delivery.status = WebhookDelivery.Status.SUCCEEDED
            delivery.delivered_at = now
            delivery.next_attempt_at = None
        else:
            wait = BACKOFF[min(delivery.attempts - 1, len(BACKOFF) - 1)]
            next_at = now + dt.timedelta(seconds=wait)
            if next_at > delivery.created_at + RETRY_WINDOW or not endpoint.is_active:
                delivery.status = WebhookDelivery.Status.FAILED
                delivery.next_attempt_at = None
            else:
                delivery.next_attempt_at = next_at
                transaction.on_commit(lambda: _queue(delivery, countdown=wait))
        delivery.save()
        return delivery.status


def retry_now(delivery, user) -> WebhookDelivery:
    """Send a failed (or waiting) delivery again now; it gets a fresh 24-hour window."""
    with tenant_context(delivery.tenant_id), transaction.atomic():
        locked = WebhookDelivery.objects.select_for_update().get(pk=delivery.pk)
        if locked.status == WebhookDelivery.Status.SUCCEEDED:
            raise ApiError("already_delivered", "This delivery already succeeded.", 409)
        locked.status = WebhookDelivery.Status.PENDING
        locked.created_at = timezone.now()
        locked.next_attempt_at = timezone.now()
        locked.save()
        write_audit(Action.UPDATE, locked, after={"retry": str(locked.event_id)}, actor=user)
        transaction.on_commit(lambda: _queue(locked))
        return locked
