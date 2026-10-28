from rest_framework import serializers

from apps.schedules.models import Attempt, Response
from apps.schedules.recordings import is_expired


class LogResponseSerializer(serializers.ModelSerializer):
    has_recording = serializers.SerializerMethodField()
    recording_expired = serializers.SerializerMethodField()

    class Meta:
        model = Response
        fields = [
            "id",
            "received_at",
            "raw_text",
            "button_payload",
            "transcript",
            "outcome",
            "confidence",
            "has_recording",
            "recording_expired",
        ]
        read_only_fields = fields

    def get_has_recording(self, obj) -> bool:
        return bool(obj.recording_key)

    def get_recording_expired(self, obj) -> bool:
        return bool(obj.recording_key) and is_expired(obj.received_at)


class LogEntrySerializer(serializers.ModelSerializer):
    """One message or call with its replies (Communication log, spec 2.8)."""

    booking_id = serializers.UUIDField(source="schedule_id")
    booking_reference = serializers.CharField(source="schedule.booking_reference")
    subscriber_id = serializers.UUIDField(source="schedule.subscriber_id")
    customer_name = serializers.CharField(source="schedule.subscriber.customer_name")
    mobile_number = serializers.CharField(source="schedule.subscriber.mobile_number")
    area_name = serializers.CharField(
        source="schedule.subscriber.area.name", allow_null=True, default=None
    )
    responses = LogResponseSerializer(many=True)

    class Meta:
        model = Attempt
        fields = [
            "id",
            "channel",
            "sent_at",
            "delivery_status",
            "failure_reason",
            "cost_paise",
            "booking_id",
            "booking_reference",
            "subscriber_id",
            "customer_name",
            "mobile_number",
            "area_name",
            "responses",
        ]
        read_only_fields = fields


class RecordingLinkSerializer(serializers.Serializer):
    url = serializers.URLField()
    expires_in = serializers.IntegerField(help_text="Seconds the link stays valid (300)")
    expires_at = serializers.DateTimeField()


class OutcomeShareSerializer(serializers.Serializer):
    outcome = serializers.CharField(help_text="An outcome, or `awaiting` for no outcome yet")
    count = serializers.IntegerField()
    share = serializers.FloatField(help_text="Percent of reminded bookings")


class ReminderResultsSerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    sent = serializers.IntegerField(help_text="WhatsApp messages sent in the range")
    delivered = serializers.IntegerField()
    failed = serializers.IntegerField()
    replied = serializers.IntegerField()
    calls = serializers.IntegerField()
    calls_answered = serializers.IntegerField()
    cost_paise = serializers.IntegerField()
    reminded = serializers.IntegerField(help_text="Bookings with a reminder in the range")
    outcomes = OutcomeShareSerializer(many=True)


class AgeingBucketSerializer(serializers.Serializer):
    bucket = serializers.ChoiceField(choices=["under_4h", "4_to_24h", "1_to_3_days", "over_3_days"])
    count = serializers.IntegerField()


class FollowUpAgeingSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    buckets = AgeingBucketSerializer(many=True)
    by_outcome = serializers.ListField(
        child=serializers.DictField(child=serializers.JSONField()),
        help_text="Per outcome: `outcome`, `total` and a count per bucket key",
    )
    generated_at = serializers.DateTimeField()


class HealthAreaSerializer(serializers.Serializer):
    area = serializers.CharField(allow_null=True)
    total = serializers.IntegerField()
    no_consent = serializers.IntegerField()
    opted_out = serializers.IntegerField()
    failed_numbers = serializers.IntegerField()


class SubscriberHealthSerializer(serializers.Serializer):
    total = serializers.IntegerField(help_text="Active subscribers")
    no_consent = serializers.IntegerField()
    opted_out = serializers.IntegerField()
    failed_numbers = serializers.IntegerField(
        help_text="Latest WhatsApp message in the last 30 days failed"
    )
    by_area = HealthAreaSerializer(many=True)
