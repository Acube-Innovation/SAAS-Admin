from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.core.validators import normalize_indian_mobile

from .models import StaffMember


class StaffMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffMember
        fields = ["id", "name", "phone", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError(_("At least 2 characters."))
        clash = StaffMember.objects.filter(
            tenant_id=self.context["request"].tenant_id, name__iexact=value
        )
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("A staff member with this name already exists."))
        return value

    def validate_phone(self, value):
        return normalize_indian_mobile(value) if value.strip() else ""


class DeliveryJobSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    printed_at = serializers.DateTimeField(allow_null=True)
    cancelled_reason = serializers.CharField(allow_blank=True)
    assigned_staff = serializers.UUIDField(source="assigned_staff_id", allow_null=True)
    assigned_staff_name = serializers.CharField(source="assigned_staff.name", default=None)
    booking_id = serializers.UUIDField(source="schedule_id")
    booking_reference = serializers.CharField(source="schedule.booking_reference")
    customer_name = serializers.CharField(source="schedule.subscriber.customer_name")
    mobile_number = serializers.CharField(source="schedule.subscriber.mobile_number")
    address = serializers.CharField(source="schedule.subscriber.address")
    cylinder_type = serializers.SerializerMethodField()
    quantity = serializers.SerializerMethodField()

    def get_cylinder_type(self, job) -> str:
        return job.schedule.custom_fields.get("cylinder_type", "")

    def get_quantity(self, job) -> int:
        return job.schedule.custom_fields.get("quantity", 1)


class DeliveryAreaSerializer(serializers.Serializer):
    area_id = serializers.UUIDField(allow_null=True)
    area_name = serializers.CharField(allow_null=True)
    default_staff_name = serializers.CharField(allow_null=True)
    jobs = DeliveryJobSerializer(many=True)


class DeliveriesSerializer(serializers.Serializer):
    date = serializers.DateField()
    areas = DeliveryAreaSerializer(many=True)
    confirmed = serializers.IntegerField()
    not_confirmed = serializers.DictField(child=serializers.IntegerField())
    changed_after_print = DeliveryJobSerializer(many=True, help_text="Printed, then cancelled")
    printed_at = serializers.DateTimeField(allow_null=True)


class PrintedSerializer(serializers.Serializer):
    date = serializers.DateField()


class PrintedResultSerializer(serializers.Serializer):
    date = serializers.DateField()
    printed = serializers.IntegerField()
    printed_at = serializers.DateTimeField()


class ReassignSerializer(serializers.Serializer):
    assigned_staff = serializers.UUIDField(allow_null=True)


class FollowUpSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    outcome = serializers.CharField()
    status = serializers.CharField()
    opened_at = serializers.DateTimeField()
    contacted_at = serializers.DateTimeField(allow_null=True)
    notes = serializers.ListField(child=serializers.DictField())
    age_minutes = serializers.SerializerMethodField()
    ageing = serializers.SerializerMethodField(
        help_text="normal (<4 h), amber (4-24 h), red (>24 h)"
    )
    booking_id = serializers.UUIDField(source="schedule_id")
    booking_reference = serializers.CharField(source="schedule.booking_reference")
    scheduled_date = serializers.DateField(source="schedule.scheduled_date", allow_null=True)
    new_date_requested = serializers.DateField(
        source="schedule.new_date_requested", allow_null=True
    )
    customer_name = serializers.CharField(source="schedule.subscriber.customer_name")
    mobile_number = serializers.CharField(source="schedule.subscriber.mobile_number")
    area_name = serializers.CharField(source="schedule.subscriber.area.name", default=None)
    last_reply = serializers.SerializerMethodField(
        help_text="The customer's latest message (needs review)"
    )
    failure_reason = serializers.SerializerMethodField()

    def _age(self, item):
        return int((timezone.now() - item.opened_at).total_seconds() // 60)

    def get_age_minutes(self, item) -> int:
        return self._age(item)

    def get_ageing(self, item) -> str:
        minutes = self._age(item)
        return "normal" if minutes < 240 else "amber" if minutes < 1440 else "red"

    def _attempts(self, item):
        return sorted(item.schedule.attempts.all(), key=lambda a: a.sent_at or a.created_at)

    def get_last_reply(self, item) -> str:
        replies = [r for a in self._attempts(item) for r in a.responses.all()]
        replies.sort(key=lambda r: r.received_at)
        if not replies:
            return ""
        return replies[-1].raw_text or replies[-1].transcript

    def get_failure_reason(self, item) -> str:
        failed = [a for a in self._attempts(item) if a.failure_reason]
        return failed[-1].failure_reason if failed else ""


class NoteSerializer(serializers.Serializer):
    text = serializers.CharField(min_length=1, max_length=1000, trim_whitespace=True)


class OptionalNoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")
