"""
Top-up, auto top-up and webhooks (portal spec 2.9). Money reaches the wallet only through
`handle_webhook()`, after the provider's signature is verified; the browser redirect never credits.
A webhook delivered twice credits once (event log + wallet idempotency key on the payment id).
"""

import datetime as dt
import logging
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.tenancy import tenant_context

from . import services
from .models import PaymentEvent, PaymentMandate, PaymentOrder, WalletTransaction
from .payments import ProviderError, ProviderUnavailable, get_provider, providers_in_order

logger = logging.getLogger(__name__)
M = PaymentMandate.Status
LIVE = (M.PENDING, M.ACTIVE, M.PAUSED)
# A debit still unanswered after this long is treated as lost, so a new one may start.
CHARGE_TIMEOUT = dt.timedelta(hours=6)


def gst_for(tenant, credit_paise: int) -> int:
    return sum(services.gst_split(credit_paise, tenant.state).values())


def _whole_rupees(value: int, low: int, high: int):
    if value % 100 or not (low <= value <= high):
        raise ApiError(
            "validation_error", f"Enter whole rupees from ₹{low // 100:,} to ₹{high // 100:,}.", 400
        )


def _with_fallback(call):
    """Try each configured provider in order; a provider that is down falls through to the next."""
    errors = []
    for provider in providers_in_order():
        try:
            return provider, call(provider)
        except ProviderUnavailable as error:
            logger.warning("payment provider %s unavailable: %s", provider.name, error)
            errors.append(f"{provider.name}: {error}")
        except ProviderError as error:
            raise ApiError(
                "payment_provider_error", "The payment provider refused the request.", 502
            ) from error
    raise ApiError(
        "payments_unavailable", "Payments are unavailable right now. Try again soon.", 503
    )


# --- manual top-up ----------------------------------------------------------------------------


def start_top_up(tenant, credit_paise: int, user):
    """Create an order for `credit_paise` of credit plus GST; returns (order, checkout)."""
    _whole_rupees(credit_paise, settings.TOP_UP_MIN_PAISE, settings.TOP_UP_MAX_PAISE)
    gst = gst_for(tenant, credit_paise)
    with tenant_context(tenant):
        order = PaymentOrder.objects.create(
            tenant=tenant,
            provider="",
            credit_paise=credit_paise,
            gst_paise=gst,
            total_paise=credit_paise + gst,
            created_by=user,
        )
        provider, checkout = _with_fallback(lambda p: p.create_order(order, tenant))
        order.provider = provider.name
        order.save()
        write_audit(Action.CREATE, order, after=snapshot(order), actor=user)
    return order, checkout


# --- auto top-up mandates ---------------------------------------------------------------------


def live_mandate(tenant):
    with tenant_context(tenant):
        return PaymentMandate.objects.filter(tenant=tenant, status__in=LIVE).first()


def start_mandate(tenant, credit_paise: int, method: str, user):
    """Ask the client to authorise a UPI AutoPay or card mandate; returns (mandate, checkout)."""
    _whole_rupees(credit_paise, settings.AUTO_TOP_UP_MIN_PAISE, settings.AUTO_TOP_UP_MAX_PAISE)
    with tenant_context(tenant), transaction.atomic():
        existing = PaymentMandate.objects.select_for_update().filter(tenant=tenant, status__in=LIVE)
        if existing.filter(status__in=(M.ACTIVE, M.PAUSED)).exists():
            raise ApiError("mandate_exists", "Auto top-up is already set up. Cancel it first.", 409)
        existing.filter(status=M.PENDING).update(status=M.FAILED, last_failure="Replaced")
        mandate = PaymentMandate.objects.create(
            tenant=tenant, provider="", method=method, credit_paise=credit_paise, created_by=user
        )
        provider, checkout = _with_fallback(lambda p: p.create_mandate(mandate, tenant))
        mandate.provider = provider.name
        mandate.save()
        write_audit(Action.CREATE, mandate, after=snapshot(mandate), actor=user)
    return mandate, checkout


def _change(mandate, user, allowed, **changes):
    with tenant_context(mandate.tenant_id), transaction.atomic():
        locked = PaymentMandate.objects.select_for_update().get(pk=mandate.pk)
        if locked.status not in allowed:
            raise ApiError(
                "invalid_transition",
                f"Auto top-up is {locked.get_status_display().lower()}.",
                409,
            )
        before = snapshot(locked)
        for name, value in changes.items():
            setattr(locked, name, value)
        locked.save()
        write_audit(Action.UPDATE, locked, before=before, after=snapshot(locked), actor=user)
        return locked


def set_mandate_amount(mandate, credit_paise: int, user):
    _whole_rupees(credit_paise, settings.AUTO_TOP_UP_MIN_PAISE, settings.AUTO_TOP_UP_MAX_PAISE)
    return _change(mandate, user, (M.ACTIVE, M.PAUSED), credit_paise=credit_paise)


def pause_mandate(mandate, user):
    return _change(mandate, user, (M.ACTIVE,), status=M.PAUSED)


def resume_mandate(mandate, user):
    mandate = _change(mandate, user, (M.PAUSED,), status=M.ACTIVE)
    tenant_id = mandate.tenant_id
    transaction.on_commit(lambda: _queue_auto_top_up(tenant_id))
    return mandate


def cancel_mandate(mandate, user):
    """Cancel locally first (no more debits), then at the provider (best effort, logged)."""
    mandate = _change(mandate, user, LIVE, status=M.CANCELLED, cancelled_at=timezone.now())
    try:
        get_provider(mandate.provider).cancel_mandate(mandate)
    except (ProviderError, ProviderUnavailable) as error:
        logger.error("cancelling mandate %s at %s failed: %s", mandate.pk, mandate.provider, error)
        with tenant_context(mandate.tenant_id):
            PaymentMandate.objects.filter(pk=mandate.pk).update(
                last_failure=f"Provider cancel failed: {error}"[:255]
            )
    return mandate


def trigger_auto_top_up(tenant) -> str:
    """
    Start one mandate debit if auto top-up is active, the wallet is below the low-balance level and
    no debit is already waiting for its webhook. Returns what happened (for logs and tests).
    """
    with tenant_context(tenant), transaction.atomic():
        mandate = (
            PaymentMandate.objects.select_for_update()
            .filter(tenant=tenant, status=M.ACTIVE)
            .first()
        )
        if mandate is None:
            return "no_active_mandate"
        if mandate.charge_in_flight and mandate.charge_started_at > timezone.now() - CHARGE_TIMEOUT:
            return "charge_in_flight"
        wallet = services.wallet_for(tenant)
        if wallet.balance_paise >= services.low_balance_level(tenant):
            return "balance_ok"
        charge_ref = f"auto_{uuid.uuid4().hex[:20]}"
        total = mandate.credit_paise + gst_for(tenant, mandate.credit_paise)
        provider = get_provider(mandate.provider)
        try:
            provider.charge_mandate(mandate, tenant, total, charge_ref)
        except (ProviderError, ProviderUnavailable) as error:
            mandate.last_failure = f"Debit not started: {error}"[:255]
            mandate.save(update_fields=["last_failure", "updated_at"])
            return "provider_error"
        mandate.charge_in_flight = charge_ref
        mandate.charge_credit_paise = mandate.credit_paise
        mandate.charge_started_at = timezone.now()
        mandate.save()
        write_audit(
            Action.UPDATE,
            mandate,
            after={"auto_top_up_started": charge_ref, "total_paise": total},
        )
        return "charge_started"


def _queue_auto_top_up(tenant_id):
    from .tasks import auto_top_up

    auto_top_up.delay(tenant_id=tenant_id)


def on_low_balance(sender, wallet, level_paise, **kwargs):
    """wallet_low_balance receiver: queue an auto top-up after the debit commits."""
    tenant_id = wallet.tenant_id
    transaction.on_commit(lambda: _queue_auto_top_up(tenant_id))


# --- webhooks ---------------------------------------------------------------------------------


def handle_webhook(provider_name: str, body: bytes, headers) -> str:
    """Verify, record once, apply. Returns the outcome (also stored on the PaymentEvent)."""
    provider = get_provider(provider_name)
    if not provider.verify(body, headers):
        raise ApiError("invalid_signature", "Webhook signature is not valid.", 400)
    event = provider.parse(body, headers)
    tenant_id = event.notes.get("tenant")
    tenant = Tenant.objects.filter(pk=tenant_id).first() if tenant_id else None
    if tenant is None:
        logger.info("payment webhook %s %s has no tenant; ignored", provider_name, event.event_id)
        return "ignored_no_tenant"
    with tenant_context(tenant), transaction.atomic():
        try:
            with transaction.atomic():
                record = PaymentEvent.objects.create(
                    tenant=tenant,
                    provider=provider_name,
                    event_id=event.event_id[:150],
                    event_type=event.raw_type[:60],
                    payload={
                        "kind": event.kind,
                        "notes": event.notes,
                        "amount": event.amount_paise,
                    },
                )
        except IntegrityError:
            return "duplicate"
        outcome = _apply(provider_name, tenant, event)
        record.outcome = outcome
        record.save(update_fields=["outcome", "updated_at"])
        return outcome


def _apply(provider_name, tenant, event) -> str:
    notes = event.notes
    if notes.get("order"):
        return _apply_order(provider_name, tenant, event)
    if notes.get("mandate") and notes.get("charge"):
        return _apply_charge(provider_name, tenant, event)
    if notes.get("mandate") or event.kind.startswith("mandate_"):
        return _apply_mandate(tenant, event)
    return "ignored"


def _apply_order(provider_name, tenant, event):
    order = (
        PaymentOrder.objects.select_for_update()
        .filter(tenant=tenant, pk=event.notes["order"], provider=provider_name)
        .first()
    )
    if order is None:
        return "unknown_order"
    if order.status == PaymentOrder.Status.PAID:
        return "already_paid"
    before = snapshot(order)
    if event.kind == "payment_failed":
        order.status = PaymentOrder.Status.FAILED
        order.failure_reason = event.reason[:255]
        order.save()
        write_audit(Action.STATUS_CHANGE, order, before=before, after=snapshot(order))
        return "order_failed"
    if event.kind != "payment_succeeded":
        return "ignored"
    if event.amount_paise != order.total_paise:
        order.status = PaymentOrder.Status.AMOUNT_MISMATCH
        order.failure_reason = f"Paid {event.amount_paise} paise, expected {order.total_paise}"
        order.save()
        write_audit(Action.STATUS_CHANGE, order, before=before, after=snapshot(order))
        return "amount_mismatch"
    reference = event.provider_payment_id or order.provider_order_id
    services.record_top_up(
        tenant, order.credit_paise, payment_reference=f"{provider_name}:{reference}"
    )
    order.status = PaymentOrder.Status.PAID
    order.provider_payment_id = event.provider_payment_id
    order.paid_at = timezone.now()
    order.save()
    write_audit(Action.STATUS_CHANGE, order, before=before, after=snapshot(order))
    return "credited"


def _apply_charge(provider_name, tenant, event):
    mandate = (
        PaymentMandate.objects.select_for_update()
        .filter(tenant=tenant, pk=event.notes["mandate"])
        .first()
    )
    if mandate is None:
        return "unknown_mandate"
    charge_ref = event.notes["charge"]
    reference = f"{provider_name}:{event.provider_payment_id or charge_ref}"
    if WalletTransaction.objects.filter(
        tenant=tenant, idempotency_key=f"payment:{reference}"
    ).exists():
        return "already_credited"
    if mandate.charge_in_flight != charge_ref:
        write_audit(
            Action.UPDATE, mandate, after={"unknown_charge": charge_ref, "kind": event.kind}
        )
        return "unknown_charge"
    before = snapshot(mandate)
    credit = mandate.charge_credit_paise or mandate.credit_paise
    mandate.charge_in_flight = ""
    mandate.charge_started_at = None
    mandate.charge_credit_paise = None
    if event.kind == "payment_failed":
        mandate.last_failure = (event.reason or "Debit failed")[:255]
        mandate.save()
        write_audit(Action.UPDATE, mandate, before=before, after=snapshot(mandate))
        return "charge_failed"
    expected = credit + gst_for(tenant, credit)
    if event.amount_paise != expected:
        mandate.last_failure = f"Paid {event.amount_paise} paise, expected {expected}"
        mandate.save()
        write_audit(Action.UPDATE, mandate, before=before, after=snapshot(mandate))
        return "amount_mismatch"
    services.record_top_up(
        tenant,
        credit,
        payment_reference=reference,
        reason=WalletTransaction.Reason.AUTO_TOP_UP,
    )
    mandate.last_charged_at = timezone.now()
    mandate.last_failure = ""
    mandate.save()
    write_audit(Action.UPDATE, mandate, before=before, after=snapshot(mandate))
    return "credited"


def _apply_mandate(tenant, event):
    mandate_id = event.notes.get("mandate")
    queryset = PaymentMandate.objects.select_for_update().filter(tenant=tenant)
    mandate = queryset.filter(pk=mandate_id).first() if mandate_id else None
    if mandate is None:
        return "unknown_mandate"
    before = snapshot(mandate)
    target = {
        "mandate_activated": (M.ACTIVE, (M.PENDING,)),
        "mandate_failed": (M.FAILED, (M.PENDING,)),
        "mandate_paused": (M.PAUSED, (M.ACTIVE,)),
        "mandate_resumed": (M.ACTIVE, (M.PAUSED,)),
        "mandate_cancelled": (M.CANCELLED, LIVE),
    }.get(event.kind)
    if target is None or mandate.status not in target[1]:
        return "ignored"
    mandate.status = target[0]
    if event.kind == "mandate_activated":
        mandate.activated_at = timezone.now()
        mandate.provider_mandate_id = event.provider_mandate_id or mandate.provider_mandate_id
        mandate.provider_customer_id = event.provider_customer_id or mandate.provider_customer_id
        tenant_id = tenant.pk
        transaction.on_commit(lambda: _queue_auto_top_up(tenant_id))
    elif event.kind == "mandate_cancelled":
        mandate.cancelled_at = timezone.now()
    elif event.kind == "mandate_failed":
        mandate.last_failure = (event.reason or "Authorisation failed")[:255]
    mandate.save()
    write_audit(Action.STATUS_CHANGE, mandate, before=before, after=snapshot(mandate))
    return f"mandate_{mandate.status}"


# --- plan -------------------------------------------------------------------------------------


def subscription_for(tenant):
    """The tenant's subscription, starting on the cheapest active plan if it has none yet."""
    from .models import Plan, Subscription

    with tenant_context(tenant):
        subscription = (
            Subscription.objects.select_related("plan", "pending_plan")
            .filter(tenant=tenant)
            .first()
        )
        if subscription:
            return subscription
        plan = Plan.objects.filter(is_active=True).order_by("monthly_fee_paise").first()
        if plan is None:
            return None
        today = timezone.localdate()
        try:
            with transaction.atomic():
                return Subscription.objects.create(
                    tenant=tenant,
                    plan=plan,
                    current_period_start=today,
                    next_billing_date=_add_month(today),
                )
        except IntegrityError:
            return Subscription.objects.get(tenant=tenant)


def _add_month(day: dt.date) -> dt.date:
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    for candidate in (day.day, 30, 29, 28):
        try:
            return dt.date(year, month, candidate)
        except ValueError:
            continue
    raise AssertionError


# --- local simulator (DEBUG only) -------------------------------------------------------------


def simulated_event(tenant, *, target, id, outcome="success", times=1) -> dict:  # noqa: A002
    """The webhook body the stub 'provider' sends for an order, a mandate or its in-flight debit."""
    success = outcome == "success"
    with tenant_context(tenant):
        if target == "order":
            order = PaymentOrder.objects.filter(tenant=tenant, pk=id, provider="stub").first()
            if order is None:
                raise ApiError("not_found", "No such sandbox order.", 404)
            return {
                "id": f"evt_order_{order.pk.hex[:12]}_{outcome}",
                "kind": "payment_succeeded" if success else "payment_failed",
                "notes": {"tenant": str(tenant.pk), "order": str(order.pk)},
                "order_id": order.provider_order_id,
                "payment_id": f"stub_pay_{order.pk.hex[:12]}",
                "amount": order.total_paise,
                "reason": "" if success else "Payment declined in the simulator",
            }
        mandate = PaymentMandate.objects.filter(tenant=tenant, pk=id, provider="stub").first()
        if mandate is None:
            raise ApiError("not_found", "No such sandbox mandate.", 404)
        notes = {"tenant": str(tenant.pk), "mandate": str(mandate.pk)}
        if target == "mandate":
            return {
                "id": f"evt_mandate_{mandate.pk.hex[:12]}_{outcome}",
                "kind": "mandate_activated" if success else "mandate_failed",
                "notes": notes,
                "mandate_id": mandate.provider_mandate_id,
                "reason": "" if success else "Authorisation declined in the simulator",
            }
        if not mandate.charge_in_flight:
            raise ApiError("no_charge", "No auto top-up debit is waiting.", 409)
        credit = mandate.charge_credit_paise or mandate.credit_paise
        return {
            "id": f"evt_charge_{mandate.charge_in_flight}_{outcome}",
            "kind": "payment_succeeded" if success else "payment_failed",
            "notes": {**notes, "charge": mandate.charge_in_flight},
            "payment_id": f"stub_pay_{mandate.charge_in_flight}",
            "amount": credit + gst_for(tenant, credit),
            "reason": "" if success else "Insufficient funds (simulator)",
        }
