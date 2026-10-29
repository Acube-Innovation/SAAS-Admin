"""
The wallet engine (portal spec 2.9, Workflow 3.4). Every balance change takes the wallet row lock
(SELECT ... FOR UPDATE) inside a transaction and writes a ledger entry; a debit that would take the
balance below zero is refused (and the database check constraint refuses it too).
"""

import datetime as dt
import math
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Min, Q, Sum
from django.dispatch import Signal
from django.utils import timezone

from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.exceptions import ApiError
from apps.core.tenancy import tenant_context

from .models import (
    DEFAULT_PRICES,
    Channel,
    Invoice,
    InvoiceSequence,
    PriceList,
    PriceOverride,
    Wallet,
    WalletTransaction,
)

T = WalletTransaction
USAGE_DAYS = 14
LOW_BALANCE_MULTIPLE = 3

# Sent (inside the debit's transaction) when the balance first falls below the low-balance level.
# Step 21's auto top-up connects here. Receivers get `wallet` and `level_paise`.
wallet_low_balance = Signal()


class InsufficientBalance(ApiError):
    def __init__(self):
        super().__init__("insufficient_balance", "The wallet balance is too low.", 402)


# --- prices -----------------------------------------------------------------------------------


def price_for(tenant, channel: str, on: dt.date | None = None) -> int:
    """The price in paise of one unit on a day: the client's override if one covers the day,
    otherwise the price list entry in effect."""
    on = on or timezone.localdate()
    with tenant_context(tenant):
        override = (
            PriceOverride.objects.filter(tenant=tenant, channel=channel, starts_on__lte=on)
            .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=on))
            .first()
        )
    if override is not None:
        return override.price_paise
    entry = (
        PriceList.objects.filter(channel=channel, effective_from__lte=on)
        .order_by("-effective_from")
        .first()
    )
    return entry.price_paise if entry else DEFAULT_PRICES[channel]


def minimum_send_price(tenant) -> int:
    """The cheapest reminder (one WhatsApp utility message): below this the wallet can't send."""
    return price_for(tenant, Channel.WHATSAPP_UTILITY)


# --- wallet -----------------------------------------------------------------------------------


def wallet_for(tenant) -> Wallet:
    with tenant_context(tenant):
        wallet = Wallet.objects.filter(tenant=tenant).first()
        if wallet is not None:
            return wallet
        try:
            with transaction.atomic():
                return Wallet.objects.create(tenant=tenant)
        except IntegrityError:  # created by a concurrent request
            return Wallet.objects.get(tenant=tenant)


def _locked(tenant) -> Wallet:
    wallet_for(tenant)
    return Wallet.objects.select_for_update().get(tenant=tenant)


def can_send(tenant) -> bool:
    """Workflow 3.4 step 3: sending pauses when the balance can't pay for one reminder."""
    with tenant_context(tenant):
        balance = wallet_for(tenant).balance_paise
        return balance >= minimum_send_price(tenant)


def debit(
    tenant, amount_paise: int, *, reference="", channel="", idempotency_key="", description=""
):
    """
    Take `amount_paise` from the wallet, or raise InsufficientBalance and change nothing. A repeated
    `idempotency_key` returns the original entry instead of charging twice.
    """
    if amount_paise <= 0:
        raise ValueError("amount_paise must be positive")
    # tenant_context() is a transaction: leave it before raising so the paused marker commits.
    with tenant_context(tenant):
        entry = _debit_locked(
            tenant, amount_paise, reference, channel, idempotency_key, description
        )
    if entry is None:  # nothing was charged
        raise InsufficientBalance()
    return entry


def _debit_locked(tenant, amount_paise, reference, channel, idempotency_key, description):
    wallet = _locked(tenant)
    if idempotency_key and (
        existing := T.objects.filter(tenant=tenant, idempotency_key=idempotency_key).first()
    ):
        return existing
    if wallet.balance_paise < amount_paise:
        if wallet.paused_at is None:
            wallet.paused_at = timezone.now()
            wallet.save(update_fields=["paused_at", "updated_at"])
        return None
    wallet.balance_paise -= amount_paise
    entry = T.objects.create(
        tenant=tenant,
        wallet=wallet,
        kind=T.Kind.DEBIT,
        reason=T.Reason.SEND,
        amount_paise=amount_paise,
        balance_after_paise=wallet.balance_paise,
        channel=channel,
        reference=reference,
        idempotency_key=idempotency_key,
        description=description,
    )
    if wallet.balance_paise < minimum_send_price(tenant) and wallet.paused_at is None:
        wallet.paused_at = timezone.now()
    level = low_balance_level(tenant)
    if wallet.balance_paise < level and wallet.low_balance_since is None:
        wallet.low_balance_since = timezone.now()
        wallet_low_balance.send(sender=Wallet, wallet=wallet, level_paise=level)
        transaction.on_commit(lambda: _notify_low_balance(tenant.pk))
    wallet.save()
    return entry


def credit(
    tenant,
    amount_paise: int,
    *,
    reason=T.Reason.TOP_UP,
    idempotency_key="",
    description="",
    actor=None,
    invoice=None,
):
    """
    Add to the wallet (from the signed payment webhook, Step 21, or a staff adjustment). Idempotent
    on `idempotency_key`. If sending was paused, it resumes and held reminders are released.
    """
    if amount_paise <= 0:
        raise ValueError("amount_paise must be positive")
    with tenant_context(tenant), transaction.atomic():
        wallet = _locked(tenant)
        if idempotency_key and (
            existing := T.objects.filter(tenant=tenant, idempotency_key=idempotency_key).first()
        ):
            return existing
        before = snapshot(wallet)
        wallet.balance_paise += amount_paise
        entry = T.objects.create(
            tenant=tenant,
            wallet=wallet,
            kind=T.Kind.CREDIT,
            reason=reason,
            amount_paise=amount_paise,
            balance_after_paise=wallet.balance_paise,
            idempotency_key=idempotency_key,
            description=description,
            invoice=invoice,
            created_by=actor,
        )
        # Sending resumes once the balance pays for a reminder; held reminders are then released.
        resumed = wallet.balance_paise >= minimum_send_price(tenant)
        if resumed:
            wallet.paused_at = None
        if wallet.balance_paise >= low_balance_level(tenant):
            wallet.low_balance_since = None
        wallet.save()
        write_audit(
            Action.UPDATE,
            wallet,
            before=before,
            after={**snapshot(wallet), "credit": entry.amount_paise, "reason": reason},
            actor=actor,
        )
        if resumed:
            release_held_reminders(tenant)
        return entry


def low_balance_level(tenant, today: dt.date | None = None) -> int:
    """
    3 × the average daily usage over the last 14 full days, or ₹1,000 until the wallet has 14 days
    of usage history.
    """
    today = today or timezone.localdate()
    with tenant_context(tenant):
        debits = T.objects.filter(tenant=tenant, kind=T.Kind.DEBIT)
        first = debits.aggregate(first=Min("created_at"))["first"]
        if first is None or (today - timezone.localtime(first).date()).days < USAGE_DAYS:
            return settings.LOW_BALANCE_DEFAULT_PAISE
        start = today - dt.timedelta(days=USAGE_DAYS)
        used = (
            debits.filter(created_at__date__gte=start, created_at__date__lt=today).aggregate(
                total=Sum("amount_paise")
            )["total"]
            or 0
        )
    return math.ceil(LOW_BALANCE_MULTIPLE * used / USAGE_DAYS)


def _notify_low_balance(tenant_id):
    from .tasks import notify_low_balance

    notify_low_balance.delay(tenant_id=tenant_id)


# --- sends ------------------------------------------------------------------------------------


def charge_attempt(attempt, minutes: int = 1):
    """Debit one reminder (the reminder engine calls this before sending). Stores the cost."""
    channel = Channel.VOICE_MINUTE if attempt.channel == "voice" else Channel.WHATSAPP_UTILITY
    units = max(1, minutes) if channel == Channel.VOICE_MINUTE else 1
    amount = price_for(attempt.tenant, channel) * units
    entry = debit(
        attempt.tenant,
        amount,
        reference=f"attempt:{attempt.pk}",
        channel=channel,
        idempotency_key=f"attempt:{attempt.pk}",
        description=f"{attempt.get_channel_display()} for {attempt.schedule.booking_reference}",
    )
    if attempt.cost_paise != entry.amount_paise:
        attempt.cost_paise = entry.amount_paise
        with tenant_context(attempt.tenant_id):
            attempt.save(update_fields=["cost_paise", "updated_at"])
    return entry


def hold_reminder(schedule):
    """Workflow 3.4 step 3: the engine found the wallet empty at send time; keep the reminder."""
    from apps.schedules.services import event

    if schedule.reminder_held_at is None:
        schedule.reminder_held_at = timezone.now()
        schedule.save(update_fields=["reminder_held_at", "updated_at"])
        event(schedule, "reminder_held", due_at=_iso(schedule.reminder_due_at))


def release_held_reminders(tenant, now=None) -> dict:
    """
    Workflow 3.4 step 4: after a credit, held reminders whose send time has passed are Skipped; the
    rest are released and go out at their time.
    """
    from apps.schedules.models import Outcome, Schedule
    from apps.schedules.services import event, record_outcome

    now = now or timezone.now()
    skipped = released = 0
    with tenant_context(tenant):
        held = Schedule.objects.filter(tenant=tenant, reminder_held_at__isnull=False)
        for schedule in held.select_for_update(of=("self",)):
            schedule.reminder_held_at = None
            schedule.save(update_fields=["reminder_held_at", "updated_at"])
            if schedule.reminder_due_at and schedule.reminder_due_at <= now:
                event(schedule, "reminder_skipped", due_at=_iso(schedule.reminder_due_at))
                record_outcome(schedule, Outcome.SKIPPED)
                skipped += 1
            else:
                event(schedule, "reminder_released", due_at=_iso(schedule.reminder_due_at))
                released += 1
    return {"skipped": skipped, "released": released}


def _iso(value):
    return value.isoformat() if value else None


# --- invoices ---------------------------------------------------------------------------------


def financial_year(day: dt.date) -> str:
    """Indian financial year (April to March), e.g. 28 Oct 2026 -> "2026-27"."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def _percent(amount_paise: int, rate: Decimal) -> int:
    return int((Decimal(amount_paise) * rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def gst_split(taxable_paise: int, buyer_state: str) -> dict:
    """CGST + SGST within the seller's state, IGST otherwise; each rounded to the paisa."""
    rate = Decimal(settings.GST_RATE_PERCENT)
    if buyer_state == settings.SELLER_STATE:
        half = _percent(taxable_paise, rate / 2)
        return {"cgst_paise": half, "sgst_paise": half, "igst_paise": 0}
    return {"cgst_paise": 0, "sgst_paise": 0, "igst_paise": _percent(taxable_paise, rate)}


def _seller():
    return {
        "legal_name": settings.SELLER_LEGAL_NAME,
        "gstin": settings.SELLER_GSTIN,
        "address": settings.SELLER_ADDRESS,
        "state": settings.SELLER_STATE,
        "email": settings.SELLER_EMAIL,
    }


def _buyer(tenant):
    return {
        "name": tenant.name,
        "legal_name": tenant.legal_name,
        "gstin": tenant.gstin or "",
        "address": ", ".join(
            p
            for p in [tenant.address_line1, tenant.address_line2, tenant.city, tenant.pin_code]
            if p
        ),
        "state": tenant.state,
        "email": tenant.contact_email,
    }


def _next_number(day: dt.date) -> tuple[str, int]:
    """The next number in the day's financial year, under a row lock (no gaps, no repeats)."""
    year = financial_year(day)
    InvoiceSequence.objects.get_or_create(financial_year=year)
    sequence = InvoiceSequence.objects.select_for_update().get(financial_year=year)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number", "updated_at"])
    return year, sequence.last_number


def issue_invoice(tenant, kind, lines, *, payment_reference="", issued_at=None) -> Invoice:
    """
    Issue a GST invoice for `lines` ([{"description", "quantity", "unit_paise"}]) and store its PDF.
    Amounts are taxable values; GST is added on top.
    """
    from .pdf import render_invoice

    issued_at = issued_at or timezone.now()
    priced = [
        {
            "description": line["description"],
            "sac": line.get("sac", settings.INVOICE_SAC_CODE),
            "quantity": line.get("quantity", 1),
            "unit_paise": line["unit_paise"],
            "amount_paise": line.get("quantity", 1) * line["unit_paise"],
        }
        for line in lines
    ]
    taxable = sum(line["amount_paise"] for line in priced)
    if taxable <= 0:
        raise ValueError("An invoice needs a positive amount")
    tax = gst_split(taxable, tenant.state)
    with tenant_context(tenant), transaction.atomic():
        year, number = _next_number(timezone.localtime(issued_at).date())
        invoice = Invoice.objects.create(
            tenant=tenant,
            number=f"{settings.INVOICE_PREFIX}/{year}/{number:06d}",
            financial_year=year,
            sequence=number,
            kind=kind,
            issued_at=issued_at,
            seller=_seller(),
            buyer=_buyer(tenant),
            place_of_supply=tenant.state,
            lines=priced,
            taxable_paise=taxable,
            total_paise=taxable + sum(tax.values()),
            payment_reference=payment_reference,
            **tax,
        )
        pdf = render_invoice(invoice)
        invoice.pdf.save(f"{invoice.number.replace('/', '-')}.pdf", pdf, save=True)
        write_audit(Action.CREATE, invoice, after=snapshot(invoice, exclude=("lines",)))
        return invoice


def record_top_up(
    tenant, credit_paise: int, *, payment_reference: str, reason=T.Reason.TOP_UP, actor=None
):
    """
    A paid top-up (GST added on top): invoice the credit value + GST, then credit the full value.
    Idempotent on the payment reference, so a repeated webhook changes nothing (Step 21).
    """
    key = f"payment:{payment_reference}"
    with tenant_context(tenant), transaction.atomic():
        _locked(tenant)  # serialise concurrent webhooks for this tenant
        if existing := T.objects.filter(tenant=tenant, idempotency_key=key).first():
            return existing
        invoice = issue_invoice(
            tenant,
            Invoice.Kind.TOP_UP,
            [
                {
                    "description": "Wallet top-up (prepaid reminder credit)",
                    "unit_paise": credit_paise,
                }
            ],
            payment_reference=payment_reference,
        )
        return credit(
            tenant,
            credit_paise,
            reason=reason,
            idempotency_key=key,
            description=f"Top-up, invoice {invoice.number}",
            actor=actor,
            invoice=invoice,
        )
