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
