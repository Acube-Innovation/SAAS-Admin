from rest_framework import serializers

from .models import Channel, Invoice, WalletTransaction


class PriceSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=Channel.choices)
    price_paise = serializers.IntegerField()


class WalletSerializer(serializers.Serializer):
    balance_paise = serializers.IntegerField()
    low_balance_level_paise = serializers.IntegerField(
        help_text="3 × average daily usage of the last 14 days, or ₹1,000 until 14 days of history"
    )
    is_low = serializers.BooleanField()
    can_send = serializers.BooleanField(help_text="False when the balance can't pay for a reminder")
    paused_at = serializers.DateTimeField(allow_null=True)
    held_reminders = serializers.IntegerField()
    prices = PriceSerializer(many=True)


class WalletTransactionSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", allow_null=True, default=None)

    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "created_at",
            "kind",
            "reason",
            "amount_paise",
            "balance_after_paise",
            "channel",
            "description",
            "invoice_id",
            "invoice_number",
        ]
        read_only_fields = fields


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = [
            "id",
            "number",
            "financial_year",
            "kind",
            "issued_at",
            "taxable_paise",
            "cgst_paise",
            "sgst_paise",
            "igst_paise",
            "total_paise",
            "payment_reference",
        ]
        read_only_fields = fields


class InvoiceLinkSerializer(serializers.Serializer):
    url = serializers.URLField()
    expires_in = serializers.IntegerField()
