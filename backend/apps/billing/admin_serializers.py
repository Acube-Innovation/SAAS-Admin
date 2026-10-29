"""Admin console billing (admin spec 2.11, 2.5 Billing tab, Workflow 3.8)."""

from rest_framework import serializers

from .models import Channel, Plan, PriceList, PriceOverride
from .serializers import InvoiceSerializer, MandateSerializer, WalletTransactionSerializer


class FeaturesSerializer(serializers.Serializer):
    voice_ai = serializers.BooleanField(default=False)
    public_api = serializers.BooleanField(default=False)
    staff_app = serializers.BooleanField(default=False)


class PlanAdminSerializer(serializers.ModelSerializer):
    features = FeaturesSerializer()

    class Meta:
        model = Plan
        fields = [
            "id",
            "code",
            "name",
            "monthly_fee_paise",
            "max_active_subscribers",
            "features",
            "is_active",
            "is_placeholder",
            "updated_at",
        ]
        read_only_fields = ["id", "is_placeholder", "updated_at"]
        extra_kwargs = {
            "name": {"min_length": 2},
            "monthly_fee_paise": {"min_value": 0, "max_value": 100_000_000},
            "max_active_subscribers": {"min_value": 1, "max_value": 10_000_000},
        }


class PlanUpdateSerializer(PlanAdminSerializer):
    class Meta(PlanAdminSerializer.Meta):
        read_only_fields = ["id", "code", "is_placeholder", "updated_at"]


class PriceEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceList
        fields = ["id", "channel", "price_paise", "effective_from", "created_at"]
        read_only_fields = fields


class PriceCreateSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=Channel.choices)
    price_paise = serializers.IntegerField(min_value=1, max_value=100_000)
    effective_from = serializers.DateField()


class ChannelPriceSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=Channel.choices)
    price_paise = serializers.IntegerField()


class PriceListAdminSerializer(serializers.Serializer):
    current = ChannelPriceSerializer(many=True)
    entries = PriceEntrySerializer(many=True)


class OverrideSerializer(serializers.ModelSerializer):
    state = serializers.SerializerMethodField(help_text="scheduled, active or ended (today, IST)")

    class Meta:
        model = PriceOverride
        fields = ["id", "channel", "price_paise", "starts_on", "ends_on", "state", "created_at"]
        read_only_fields = fields

    def get_state(self, obj) -> str:
        today = self.context["today"]
        if obj.starts_on > today:
            return "scheduled"
        if obj.ends_on is not None and obj.ends_on < today:
            return "ended"
        return "active"


class OverrideCreateSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=Channel.choices)
    price_paise = serializers.IntegerField(min_value=0, max_value=100_000)
    starts_on = serializers.DateField()
    ends_on = serializers.DateField(required=False, allow_null=True, default=None)


class OverrideEndSerializer(serializers.Serializer):
    ends_on = serializers.DateField(allow_null=True)


class EffectivePriceSerializer(ChannelPriceSerializer):
    source = serializers.ChoiceField(choices=["price_list", "override"])


class SubscriptionAdminSerializer(serializers.Serializer):
    plan = PlanAdminSerializer(allow_null=True)
    pending_plan = PlanAdminSerializer(allow_null=True)
    status = serializers.CharField(allow_null=True)
    current_period_start = serializers.DateField(allow_null=True)
    next_billing_date = serializers.DateField(
        allow_null=True, help_text="A pending plan applies on this day"
    )


class WalletAdminSerializer(serializers.Serializer):
    balance_paise = serializers.IntegerField()
    low_balance_level_paise = serializers.IntegerField()
    is_low = serializers.BooleanField()
    can_send = serializers.BooleanField()
    paused_at = serializers.DateTimeField(allow_null=True)


class TenantBillingSerializer(serializers.Serializer):
    subscription = SubscriptionAdminSerializer()
    wallet = WalletAdminSerializer()
    mandate = MandateSerializer(allow_null=True)
    prices = EffectivePriceSerializer(many=True)
    active_subscribers = serializers.IntegerField()
    recent_transactions = WalletTransactionSerializer(many=True)
    invoices = InvoiceSerializer(many=True)


class ChangePlanSerializer(serializers.Serializer):
    plan_code = serializers.SlugField()
