from rest_framework import serializers

from .models import EVENT_TYPES, ApiKey, WebhookDelivery, WebhookEndpoint


class ApiKeySerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(
        source="created_by.email", allow_null=True, default=None
    )

    class Meta:
        model = ApiKey
        fields = [
            "id",
            "name",
            "prefix",
            "rate_limit_per_minute",
            "created_at",
            "created_by_email",
            "last_used_at",
            "revoked_at",
        ]
        read_only_fields = fields


class ApiKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=2, max_length=100)


class ApiKeyCreatedSerializer(serializers.Serializer):
    key = ApiKeySerializer()
    secret = serializers.CharField(help_text="The full API key. Shown only in this response.")


class EndpointSerializer(serializers.ModelSerializer):
    events = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = WebhookEndpoint
        fields = ["id", "url", "events", "secret", "is_active", "created_at"]
        read_only_fields = fields


class EndpointCreateSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=500)
    events = serializers.ListField(child=serializers.ChoiceField(choices=EVENT_TYPES), min_length=1)


class EndpointUpdateSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=500, required=False)
    events = serializers.ListField(
        child=serializers.ChoiceField(choices=EVENT_TYPES), min_length=1, required=False
    )
    is_active = serializers.BooleanField(required=False)


class DeliverySerializer(serializers.ModelSerializer):
    endpoint_url = serializers.CharField(source="endpoint.url")

    class Meta:
        model = WebhookDelivery
        fields = [
            "id",
            "event_id",
            "event_type",
            "endpoint_id",
            "endpoint_url",
            "status",
            "attempts",
            "last_status_code",
            "last_error",
            "last_attempt_at",
            "next_attempt_at",
            "delivered_at",
            "created_at",
            "payload",
        ]
        read_only_fields = fields


class EventTypeSerializer(serializers.Serializer):
    code = serializers.CharField()
    label = serializers.CharField()


class OutcomeEventSerializer(serializers.Serializer):
    """One recorded answer (a ScheduleEvent of kind outcome_set)."""

    booking_reference = serializers.CharField(source="schedule.booking_reference")
    subscriber_ref = serializers.CharField(source="schedule.subscriber.external_ref")
    outcome = serializers.SerializerMethodField()
    new_date_requested = serializers.DateField(
        source="schedule.new_date_requested", allow_null=True
    )
    recorded_at = serializers.DateTimeField(source="occurred_at")

    def get_outcome(self, obj) -> str:
        return obj.data.get("outcome", "")
