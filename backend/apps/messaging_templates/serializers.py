from rest_framework import serializers

from apps.core.constants import Language
from apps.core.models import Tenant
from apps.industry.models import ScheduleType

from .models import TemplateStatusEvent, WhatsAppTemplate, WhatsAppTemplateVersion
from .services import TRANSITIONS
from .validation import NAME_RE, OUTCOMES

# Actions the admin console offers as buttons (provider and client actions happen elsewhere).
ADMIN_ACTIONS = (
    "edit",
    "send_to_client",
    "manual_signoff",
    "submit",
    "make_live",
    "disable",
    "new_version",
)


class ButtonSerializer(serializers.Serializer):
    text = serializers.CharField(allow_blank=True)
    outcome = serializers.ChoiceField(choices=OUTCOMES)


class ContentSerializer(serializers.Serializer):
    """Template content; the spec 2.7 rules are applied by validation.validate_content()."""

    header_text = serializers.CharField(required=False, allow_blank=True, default="")
    body = serializers.CharField(allow_blank=True)
    footer = serializers.CharField(required=False, allow_blank=True, default="")
    buttons = ButtonSerializer(many=True, required=False, default=list)
    sample_values = serializers.DictField(
        child=serializers.CharField(allow_blank=True), required=False, default=dict
    )


class VersionEditSerializer(ContentSerializer):
    body = serializers.CharField(allow_blank=True, required=False)
    header_text = serializers.CharField(required=False, allow_blank=True)
    footer = serializers.CharField(required=False, allow_blank=True)
    buttons = ButtonSerializer(many=True, required=False)
    sample_values = serializers.DictField(
        child=serializers.CharField(allow_blank=True), required=False
    )


class TemplateCreateSerializer(ContentSerializer):
    tenant = serializers.PrimaryKeyRelatedField(queryset=Tenant.objects.all())
    name = serializers.RegexField(
        NAME_RE,
        max_length=512,
        error_messages={"invalid": "Lowercase letters, digits and underscores only."},
    )
    language = serializers.ChoiceField(choices=Language.choices)
    category = serializers.ChoiceField(
        choices=WhatsAppTemplate.Category.choices, default=WhatsAppTemplate.Category.UTILITY
    )
    schedule_type = serializers.PrimaryKeyRelatedField(
        queryset=ScheduleType.objects.filter(is_active=True)
    )


class ReasonOptionalSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class ManualSignoffSerializer(serializers.Serializer):
    evidence = serializers.FileField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class EventSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", allow_null=True, default=None)

    class Meta:
        model = TemplateStatusEvent
        fields = ["action", "from_status", "to_status", "actor_email", "note", "created_at"]


class VersionSerializer(serializers.ModelSerializer):
    has_evidence = serializers.SerializerMethodField()
    signed_off_by_email = serializers.EmailField(
        source="signed_off_by.email", allow_null=True, default=None
    )
    available_actions = serializers.SerializerMethodField()
    events = EventSerializer(many=True, read_only=True)

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
            "provider_template_id",
            "rejection_reason",
            "has_evidence",
            "signoff_method",
            "signed_off_by_email",
            "signed_off_at",
            "submitted_at",
            "approved_at",
            "created_at",
            "available_actions",
            "events",
        ]

    def get_has_evidence(self, obj) -> bool:
        return bool(obj.signoff_evidence)

    def get_available_actions(self, obj) -> list[str]:
        """Actions the version's status allows (the user's role is checked by the API)."""
        actions = [a for a in ADMIN_ACTIONS if obj.status in TRANSITIONS[a][0]]
        if obj.is_live and "make_live" in actions:
            actions.remove("make_live")
        return actions


class TemplateSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.name")
    schedule_type_name = serializers.CharField(source="schedule_type.name")
    live_version = serializers.SerializerMethodField()
    latest_version = serializers.SerializerMethodField()

    class Meta:
        model = WhatsAppTemplate
        fields = [
            "id",
            "tenant_id",
            "tenant_name",
            "name",
            "language",
            "category",
            "schedule_type",
            "schedule_type_name",
            "live_version",
            "latest_version",
            "created_at",
        ]

    def get_live_version(self, obj) -> int | None:
        return next((v.version for v in obj.versions.all() if v.is_live), None)

    def get_latest_version(self, obj) -> dict:
        latest = max(obj.versions.all(), key=lambda v: v.version)
        return {"id": str(latest.pk), "version": latest.version, "status": latest.status}


class TemplateDetailSerializer(TemplateSerializer):
    versions = VersionSerializer(many=True, read_only=True)

    class Meta(TemplateSerializer.Meta):
        fields = [*TemplateSerializer.Meta.fields, "versions"]
