"""Editor payload and read shapes for industry templates (spec 2.6)."""

import re

from django.utils.translation import gettext as _
from rest_framework import serializers

from .models import FieldDefinition, IndustryTemplate, IndustryTemplateVersion, ScheduleType

TEMPLATE_KEY_RE = r"^[a-z][a-z0-9_]{2,49}$"
FIELD_KEY_RE = r"^[a-z][a-z0-9_]{1,49}$"
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
OUTCOMES = ("confirmed", "not_available", "reschedule")
CHANNELS = ("whatsapp", "voice")
BUTTON_MAX = 25  # WhatsApp quick-reply limit (spec 2.7)


class FieldInputSerializer(serializers.Serializer):
    key = serializers.RegexField(
        FIELD_KEY_RE, error_messages={"invalid": _("Use snake_case: letters, digits, underscores.")}
    )
    label = serializers.CharField(min_length=1, max_length=100)
    field_type = serializers.ChoiceField(choices=FieldDefinition.FieldType.choices)
    required = serializers.BooleanField(default=False)
    is_core = serializers.BooleanField(default=False, read_only=True)
    options = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )
    max_length = serializers.IntegerField(
        min_value=1, max_value=1000, required=False, allow_null=True
    )
    is_active = serializers.BooleanField(default=True)

    def validate(self, attrs):
        options = attrs.get("options") or []
        if len(set(options)) != len(options):
            raise serializers.ValidationError({"options": [_("Options must be unique.")]})
        if attrs["field_type"] != "choice" and options:
            raise serializers.ValidationError({"options": [_("Only choice fields have options.")]})
        return attrs


class ReminderRuleSerializer(serializers.Serializer):
    send_time = serializers.CharField()
    days_before = serializers.IntegerField(min_value=0, max_value=7)
    wait_minutes = serializers.IntegerField(min_value=30, max_value=1440)
    quiet_start = serializers.CharField()
    quiet_end = serializers.CharField()
    delivery_weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6), min_length=1, max_length=7
    )
    channel_order = serializers.ListField(
        child=serializers.ChoiceField(choices=CHANNELS), min_length=1, max_length=2
    )

    def _time(self, value):
        if not TIME_RE.match(value):
            raise serializers.ValidationError(_("Use HH:MM (24-hour)."))
        return value

    validate_send_time = validate_quiet_start = validate_quiet_end = _time

    def validate_delivery_weekdays(self, value):
        return sorted(set(value))


class AnswerSerializer(serializers.Serializer):
    key = serializers.RegexField(FIELD_KEY_RE)
    label = serializers.CharField(max_length=BUTTON_MAX)
    outcome = serializers.ChoiceField(choices=OUTCOMES)
    pick_next_delivery_days = serializers.IntegerField(min_value=1, max_value=10, required=False)


class QuestionFlowSerializer(serializers.Serializer):
    answers = AnswerSerializer(many=True, min_length=1, max_length=3)

    def validate_answers(self, answers):
        if len({a["key"] for a in answers}) != len(answers):
            raise serializers.ValidationError(_("Answer keys must be unique."))
        return answers


class ScheduleTypeInputSerializer(serializers.Serializer):
    key = serializers.RegexField(FIELD_KEY_RE)
    name = serializers.CharField(min_length=2, max_length=100)
    is_active = serializers.BooleanField(default=True)
    fields = FieldInputSerializer(many=True)
    default_reminder_rule = ReminderRuleSerializer()
    default_question_flow = QuestionFlowSerializer()

    def validate_fields(self, fields):
        _unique_keys(fields)
        return fields


class TemplateInputSerializer(serializers.Serializer):
    """The whole template as the editor saves it. Saving always creates a new version."""

    key = serializers.RegexField(
        TEMPLATE_KEY_RE,
        error_messages={"invalid": _("3-50 characters: lowercase letters, digits, underscores.")},
    )
    name = serializers.CharField(min_length=2, max_length=100)
    description = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default=""
    )
    is_active = serializers.BooleanField(default=True)
    subscriber_fields = FieldInputSerializer(many=True)
    schedule_types = ScheduleTypeInputSerializer(many=True, min_length=1)

    def validate_subscriber_fields(self, fields):
        _unique_keys(fields)
        return fields

    def validate_schedule_types(self, schedule_types):
        _unique_keys(schedule_types)
        return schedule_types


def _unique_keys(items):
    keys = [item["key"] for item in items]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise serializers.ValidationError(_("Duplicate keys: %s") % ", ".join(duplicates))


# --- read shapes -------------------------------------------------------------------------------


class FieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = FieldDefinition
        fields = [
            "key",
            "label",
            "field_type",
            "required",
            "is_core",
            "options",
            "max_length",
            "is_active",
        ]


class ScheduleTypeSerializer(serializers.ModelSerializer):
    fields = FieldSerializer(many=True)

    class Meta:
        model = ScheduleType
        fields = [
            "id",
            "key",
            "name",
            "is_active",
            "fields",
            "default_reminder_rule",
            "default_question_flow",
        ]


class IndustryTemplateSerializer(serializers.ModelSerializer):
    subscriber_fields = serializers.SerializerMethodField()
    schedule_types = ScheduleTypeSerializer(many=True)
    clients_using = serializers.IntegerField(read_only=True)

    class Meta:
        model = IndustryTemplate
        fields = [
            "id",
            "key",
            "name",
            "description",
            "is_active",
            "current_version",
            "clients_using",
            "subscriber_fields",
            "schedule_types",
            "updated_at",
        ]

    def get_subscriber_fields(self, obj) -> list[dict]:
        fields = [f for f in obj.fields.all() if f.entity == FieldDefinition.Entity.SUBSCRIBER]
        return FieldSerializer(fields, many=True).data


class IndustryTemplateVersionSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", allow_null=True)

    class Meta:
        model = IndustryTemplateVersion
        fields = ["version", "definition", "created_by_email", "created_at"]
