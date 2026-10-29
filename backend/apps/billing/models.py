"""
Wallet, prices and GST invoices (portal spec 2.9, Workflow 3.4; admin spec 2.11).
All money is in paise.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel, TenantScopedModel


class Channel(models.TextChoices):
    WHATSAPP_UTILITY = "whatsapp_utility", _("WhatsApp utility message")
    WHATSAPP_SERVICE = "whatsapp_service", _("WhatsApp service message")
    VOICE_MINUTE = "voice_minute", _("Voice call minute")


# Prices seeded by migration 0002 and used while the price list has no entry for a channel.
DEFAULT_PRICES = {"whatsapp_utility": 85, "whatsapp_service": 35, "voice_minute": 150}


class PriceList(BaseModel):
    """Platform price per channel from a date (newest effective entry wins). Not tenant-scoped."""

    channel = models.CharField(max_length=20, choices=Channel.choices)
    price_paise = models.PositiveIntegerField()
    effective_from = models.DateField()

    class Meta:
        ordering = ["channel", "-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["channel", "effective_from"], name="price_per_day")
        ]

    def __str__(self):
        return f"{self.channel} {self.price_paise} paise from {self.effective_from}"


class Wallet(TenantScopedModel):
    """One per tenant. The database refuses a negative balance, as a last line of defence."""

    balance_paise = models.BigIntegerField(default=0)
    low_balance_since = models.DateTimeField(
        null=True, blank=True, help_text="Set when the balance fell below the low-balance level"
    )
    paused_at = models.DateTimeField(
        null=True, blank=True, help_text="Set when sending paused for lack of balance"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant"], name="one_wallet_per_tenant"),
            models.CheckConstraint(condition=Q(balance_paise__gte=0), name="wallet_not_negative"),
        ]

    def __str__(self):
        return f"Wallet {self.tenant_id}: {self.balance_paise} paise"


class WalletTransaction(TenantScopedModel):
    """An append-only ledger entry. `balance_after_paise` makes every statement line checkable."""

    class Kind(models.TextChoices):
        CREDIT = "credit", _("Credit")
        DEBIT = "debit", _("Debit")

    class Reason(models.TextChoices):
        TOP_UP = "top_up", _("Top-up")
        AUTO_TOP_UP = "auto_top_up", _("Auto top-up")
        SEND = "send", _("Message or call")
        ADJUSTMENT = "adjustment", _("Adjustment")

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    amount_paise = models.PositiveBigIntegerField()
    balance_after_paise = models.BigIntegerField()
    channel = models.CharField(max_length=20, choices=Channel.choices, blank=True)
    reference = models.CharField(max_length=100, blank=True, help_text="e.g. attempt:<id>")
    idempotency_key = models.CharField(max_length=150, blank=True)
    description = models.CharField(max_length=255, blank=True)
    invoice = models.ForeignKey(
        "Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="wallet_txn_idempotent",
            ),
            models.CheckConstraint(condition=Q(amount_paise__gt=0), name="wallet_txn_positive"),
        ]
        indexes = [models.Index(fields=["tenant", "kind", "created_at"], name="wallet_txn_usage")]


class InvoiceSequence(BaseModel):
    """The last invoice number used in a financial year. Not tenant-scoped: numbers are global."""

    financial_year = models.CharField(max_length=7, unique=True)  # e.g. 2026-27
    last_number = models.PositiveIntegerField(default=0)


def invoice_upload_to(instance, filename):
    return f"tenants/{instance.tenant_id}/invoices/{instance.financial_year}/{filename}"


class Invoice(TenantScopedModel):
    """A GST tax invoice. Seller and buyer details are copied in, so a reissue never changes."""

    class Kind(models.TextChoices):
        TOP_UP = "top_up", _("Wallet top-up")
        SUBSCRIPTION = "subscription", _("Subscription")

    number = models.CharField(max_length=30, unique=True)  # SC/2026-27/000123
    financial_year = models.CharField(max_length=7)
    sequence = models.PositiveIntegerField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    issued_at = models.DateTimeField()
    seller = models.JSONField()
    buyer = models.JSONField()
    place_of_supply = models.CharField(max_length=2, help_text="State code (buyer's state)")
    lines = models.JSONField(
        help_text='[{"description", "sac", "quantity", "unit_paise", "amount_paise"}]'
    )
    taxable_paise = models.PositiveBigIntegerField()
    cgst_paise = models.PositiveBigIntegerField(default=0)
    sgst_paise = models.PositiveBigIntegerField(default=0)
    igst_paise = models.PositiveBigIntegerField(default=0)
    total_paise = models.PositiveBigIntegerField()
    payment_reference = models.CharField(max_length=100, blank=True)
    pdf = models.FileField(upload_to=invoice_upload_to, blank=True, max_length=300)

    class Meta:
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["financial_year", "sequence"], name="invoice_sequence_per_year"
            )
        ]

    def __str__(self):
        return self.number


# --- plans (Step 21 portal view; admin management in Step A2) ---------------------------------


class Plan(BaseModel):
    """A subscription plan (admin spec 2.11). Not tenant-scoped."""

    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    monthly_fee_paise = models.PositiveIntegerField()
    max_active_subscribers = models.PositiveIntegerField()
    features = models.JSONField(
        default=dict,
        blank=True,
        help_text='{"voice_ai": bool, "public_api": bool, "staff_app": bool}',
    )
    is_active = models.BooleanField(default=True)
    is_placeholder = models.BooleanField(
        default=False, help_text="Seeded until the approved plan list replaces it"
    )

    class Meta:
        ordering = ["monthly_fee_paise"]

    def __str__(self):
        return self.name


class Subscription(TenantScopedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        CANCELLED = "cancelled", _("Cancelled")

    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    current_period_start = models.DateField()
    next_billing_date = models.DateField()
    pending_plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant"], name="one_subscription_per_tenant")
        ]


# --- payments (portal spec 2.9) ---------------------------------------------------------------


class Provider(models.TextChoices):
    RAZORPAY = "razorpay", _("Razorpay")
    CASHFREE = "cashfree", _("Cashfree")
    STUB = "stub", _("Sandbox simulator")


class PaymentOrder(TenantScopedModel):
    """A manual top-up checkout. The wallet is credited only from the provider's signed webhook."""

    class Status(models.TextChoices):
        CREATED = "created", _("Waiting for payment")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        AMOUNT_MISMATCH = "amount_mismatch", _("Paid amount didn't match")

    provider = models.CharField(max_length=10, choices=Provider.choices)
    credit_paise = models.PositiveBigIntegerField(help_text="Wallet credit (taxable value)")
    gst_paise = models.PositiveBigIntegerField()
    total_paise = models.PositiveBigIntegerField(help_text="What the client pays")
    provider_order_id = models.CharField(max_length=100, blank=True)
    provider_payment_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)
    failure_reason = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_order_id"],
                condition=~Q(provider_order_id=""),
                name="payment_order_provider_id",
            )
        ]


class PaymentMandate(TenantScopedModel):
    """UPI AutoPay or card mandate for auto top-up (one live mandate per tenant)."""

    class Method(models.TextChoices):
        UPI = "upi", _("UPI AutoPay")
        CARD = "card", _("Card")

    class Status(models.TextChoices):
        PENDING = "pending", _("Waiting for authorisation")
        ACTIVE = "active", _("Active")
        PAUSED = "paused", _("Paused")
        CANCELLED = "cancelled", _("Cancelled")
        FAILED = "failed", _("Failed")

    provider = models.CharField(max_length=10, choices=Provider.choices)
    method = models.CharField(max_length=10, choices=Method.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    credit_paise = models.PositiveBigIntegerField(
        help_text="Credit per auto top-up (₹1,000–₹15,000)"
    )
    provider_mandate_id = models.CharField(max_length=100, blank=True)
    provider_customer_id = models.CharField(max_length=100, blank=True)
    charge_in_flight = models.CharField(
        max_length=100, blank=True, help_text="Provider reference of a debit awaiting its webhook"
    )
    charge_credit_paise = models.PositiveBigIntegerField(
        null=True, blank=True, help_text="Credit the in-flight debit pays for"
    )
    charge_started_at = models.DateTimeField(null=True, blank=True)
    last_charged_at = models.DateTimeField(null=True, blank=True)
    last_failure = models.CharField(max_length=255, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"],
                condition=Q(status__in=["pending", "active", "paused"]),
                name="one_live_mandate_per_tenant",
            )
        ]


class PaymentEvent(TenantScopedModel):
    """Every verified webhook, once (provider + event id). Replays are recorded as duplicates."""

    provider = models.CharField(max_length=10, choices=Provider.choices)
    event_id = models.CharField(max_length=150)
    event_type = models.CharField(max_length=60)
    payload = models.JSONField()
    outcome = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["provider", "event_id"], name="payment_event_once")
        ]


class PriceOverride(TenantScopedModel):
    """A client's own price for a channel over a date range (admin spec 2.11). No backdating or
    overlaps for the same channel; `ends_on` empty means open-ended."""

    channel = models.CharField(max_length=20, choices=Channel.choices)
    price_paise = models.PositiveIntegerField()
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        ordering = ["channel", "starts_on"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(ends_on__gte=models.F("starts_on")),
                name="price_override_dates",
            )
        ]
