from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.messaging_templates.models import (
    TemplateStatusEvent,
    VersionStatus,
    VoiceScript,
    WhatsAppTemplate,
    WhatsAppTemplateVersion,
)
from apps.messaging_templates.serializers import ButtonSerializer

S = VersionStatus
# Versions the client sees besides the live one. Drafts stay with platform staff.
CLIENT_VISIBLE = (S.SENT_TO_CLIENT, S.SIGNED_OFF, S.SENT_BACK, S.SUBMITTED, S.APPROVED, S.REJECTED)
# Version history entries shown to the client.
CLIENT_EVENTS = (
    "send_to_client",
    "client_approve",
    "client_send_back",
    "manual_signoff",
    "make_live",
)


class ClientVersionSerializer(serializers.ModelSerializer):
    buttons = ButtonSerializer(many=True)
    can_review = serializers.SerializerMethodField(
        help_text="Waiting for the client's Approve or Send back"
    )
    signed_off_by_name = serializers.SerializerMethodField()
    sent_back_comment = serializers.SerializerMethodField()

    class Meta:
        model = WhatsAppTemplateVersion
        fields = [
            "id",
            "version",
            "status",
            "is_live",
            "header_text",
            "body",
            "footer",
            "buttons",
            "sample_values",
            "can_review",
            "signoff_method",
            "signed_off_by_name",
            "signed_off_at",
            "approved_at",
            "sent_back_comment",
        ]
        read_only_fields = fields

    def get_can_review(self, obj) -> bool:
        return obj.status == S.SENT_TO_CLIENT

    def get_signed_off_by_name(self, obj) -> str | None:
        user = obj.signed_off_by
        if user is None:
            return None
        return user.full_name or user.email

    def get_sent_back_comment(self, obj) -> str | None:
        if obj.status != S.SENT_BACK:
            return None
        event = next(
            (e for e in reversed(obj.events.all()) if e.action == "client_send_back"), None
        )
        return event.note if event else None


class ClientEventSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(source="version.version")
    by = serializers.SerializerMethodField(help_text="The client user; null for platform staff")

    class Meta:
        model = TemplateStatusEvent
        fields = ["action", "version", "by", "note", "created_at"]
        read_only_fields = fields

    def get_by(self, obj) -> str | None:
        actor = obj.actor
        if actor is None or actor.is_platform_staff:
            return None
        return actor.full_name or actor.email


class ClientTemplateSerializer(serializers.ModelSerializer):
    schedule_type_name = serializers.CharField(source="schedule_type.name")
    live = serializers.SerializerMethodField()
    pending = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()

    class Meta:
        model = WhatsAppTemplate
        fields = ["id", "name", "language", "schedule_type_name", "live", "pending", "history"]
        read_only_fields = fields

    def _versions(self, obj):
        return sorted(obj.versions.all(), key=lambda v: v.version, reverse=True)

    @extend_schema_field(ClientVersionSerializer(allow_null=True))
    def get_live(self, obj):
        live = next((v for v in self._versions(obj) if v.is_live), None)
        return ClientVersionSerializer(live).data if live else None

    @extend_schema_field(ClientVersionSerializer(allow_null=True))
    def get_pending(self, obj):
        """The newest version, if it is newer than the live one and has reached the client."""
        versions = self._versions(obj)
        latest = versions[0] if versions else None
        if latest is None or latest.is_live or latest.status not in CLIENT_VISIBLE:
            return None
        return ClientVersionSerializer(latest).data

    @extend_schema_field(ClientEventSerializer(many=True))
    def get_history(self, obj):
        events = [
            e for v in obj.versions.all() for e in v.events.all() if e.action in CLIENT_EVENTS
        ]
        events.sort(key=lambda e: e.created_at, reverse=True)
        return ClientEventSerializer(events, many=True).data


class SendBackSerializer(serializers.Serializer):
    comment = serializers.CharField(min_length=5, max_length=1000)

    def validate_comment(self, value):
        value = value.strip()
        if len(value) < 5:
            raise serializers.ValidationError("Say what should change (at least 5 characters).")
        return value


class VoiceAnswerSerializer(serializers.Serializer):
    text = serializers.CharField()
    outcome = serializers.CharField()


class VoiceLiveSerializer(serializers.Serializer):
    version = serializers.IntegerField()
    greeting = serializers.CharField()
    question = serializers.CharField()
    answers = VoiceAnswerSerializer(many=True)
    voice = serializers.CharField()
    max_retries = serializers.IntegerField()
    retry_gap_minutes = serializers.IntegerField()


class ClientVoiceScriptSerializer(serializers.ModelSerializer):
    schedule_type_name = serializers.CharField(source="schedule_type.name")
    live = serializers.SerializerMethodField()

    class Meta:
        model = VoiceScript
        fields = ["id", "name", "language", "schedule_type_name", "live"]
        read_only_fields = fields

    @extend_schema_field(VoiceLiveSerializer(allow_null=True))
    def get_live(self, obj):
        live = next((v for v in obj.versions.all() if v.status == "live"), None)
        return VoiceLiveSerializer(live).data if live else None


class FlowAnswerSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    outcome = serializers.CharField()
    pick_next_delivery_days = serializers.IntegerField(required=False)


class QuestionFlowSerializer(serializers.Serializer):
    schedule_type = serializers.CharField()
    name = serializers.CharField()
    answers = FlowAnswerSerializer(many=True)
