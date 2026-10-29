from rest_framework import serializers

from .models import Channel, Invoice, Provider, WalletTransaction


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


# --- payments (Step 21) -----------------------------------------------------------------------


class CheckoutSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=Provider.choices)
    data = serializers.DictField(help_text="What the provider's browser checkout needs")


class PaymentOrderSerializer(serializers.ModelSerializer):
    class Meta:
        from .models import PaymentOrder

        model = PaymentOrder
        fields = [
            "id",
            "provider",
            "credit_paise",
            "gst_paise",
            "total_paise",
            "status",
            "failure_reason",
            "created_at",
            "paid_at",
        ]
        read_only_fields = fields


class TopUpCreateSerializer(serializers.Serializer):
    credit_paise = serializers.IntegerField(help_text="Wallet credit; GST is added on top")


class TopUpStartedSerializer(serializers.Serializer):
    order = PaymentOrderSerializer()
    checkout = CheckoutSerializer()


class MandateSerializer(serializers.ModelSerializer):
    gst_paise = serializers.SerializerMethodField()
    charge_in_progress = serializers.SerializerMethodField()

    class Meta:
        from .models import PaymentMandate

        model = PaymentMandate
        fields = [
            "id",
            "provider",
            "method",
            "status",
            "credit_paise",
            "gst_paise",
            "charge_in_progress",
            "last_charged_at",
            "last_failure",
            "activated_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_gst_paise(self, obj) -> int:
        from .payment_services import gst_for

        return gst_for(obj.tenant, obj.credit_paise)

    def get_charge_in_progress(self, obj) -> bool:
        return bool(obj.charge_in_flight)


class AutoTopUpSerializer(serializers.Serializer):
    mandate = MandateSerializer(allow_null=True)
    min_credit_paise = serializers.IntegerField()
    max_credit_paise = serializers.IntegerField()
    low_balance_level_paise = serializers.IntegerField()


class MandateCreateSerializer(serializers.Serializer):
    credit_paise = serializers.IntegerField()
    method = serializers.ChoiceField(choices=["upi", "card"])


class MandateAmountSerializer(serializers.Serializer):
    credit_paise = serializers.IntegerField()


class MandateStartedSerializer(serializers.Serializer):
    mandate = MandateSerializer()
    checkout = CheckoutSerializer()


class PlanSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    monthly_fee_paise = serializers.IntegerField()
    max_active_subscribers = serializers.IntegerField()
    features = serializers.DictField(child=serializers.BooleanField())
    is_placeholder = serializers.BooleanField()


class PlanViewSerializer(serializers.Serializer):
    plan = PlanSerializer(allow_null=True)
    pending_plan = PlanSerializer(allow_null=True)
    status = serializers.CharField(allow_null=True)
    current_period_start = serializers.DateField(allow_null=True)
    next_billing_date = serializers.DateField(allow_null=True)
    active_subscribers = serializers.IntegerField()
    plans = PlanSerializer(many=True)


class PlanChangeRequestSerializer(serializers.Serializer):
    plan_code = serializers.SlugField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")


class PaymentSimulateSerializer(serializers.Serializer):
    target = serializers.ChoiceField(choices=["order", "mandate", "charge"])
    id = serializers.UUIDField(help_text="PaymentOrder or PaymentMandate id")
    outcome = serializers.ChoiceField(choices=["success", "failure"], default="success")
    times = serializers.IntegerField(
        min_value=1, max_value=3, default=1, help_text="Deliver N times"
    )
