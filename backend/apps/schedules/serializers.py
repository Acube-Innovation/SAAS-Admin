from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.industry.fields import clean_custom_fields, custom_field_definitions
from apps.industry.models import ScheduleType
from apps.jobs.models import FollowUpItem, Job
from apps.subscribers.models import Subscriber
from apps.subscribers.serializers import TenantScopedPK

from .models import Schedule

# Spec 2.3: quantity is a whole number 1-99, default 1 (the template only says "number").
QUANTITY_RANGE = (1, 99)


def clean_schedule_custom_fields(tenant, schedule_type_key, values, existing=None, partial=False):
    definitions = custom_field_definitions(tenant, "schedule", schedule_type_key)
    keys = {d["key"] for d in definitions}
    values = dict(values or {})
    if "quantity" in keys and existing is None and values.get("quantity") in (None, ""):
        values["quantity"] = 1
    try:
        cleaned = clean_custom_fields(definitions, values, existing=existing, partial=partial)
    except serializers.ValidationError as exc:
        raise serializers.ValidationError({"custom_fields": exc.detail}) from None
    quantity = cleaned.get("quantity")
    if "quantity" in keys and quantity is not None:
        low, high = QUANTITY_RANGE
        if not isinstance(quantity, int) or not low <= quantity <= high:
            raise serializers.ValidationError(
                {"custom_fields": {"quantity": [_("A whole number from 1 to 99.")]}}
            )
    return cleaned


class JobSummarySerializer(serializers.ModelSerializer):
    assigned_staff_name = serializers.CharField(
        source="assigned_staff.name", read_only=True, default=None
    )

    class Meta:
        model = Job
        fields = ["id", "scheduled_date", "assigned_staff", "assigned_staff_name", "status"]


class FollowUpSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = FollowUpItem
        fields = ["id", "outcome", "status", "opened_at", "contacted_at"]


class BookingSerializer(serializers.ModelSerializer):
    subscriber = TenantScopedPK(queryset=Subscriber.objects.all(), required=False)
    subscriber_ref = serializers.CharField(
        write_only=True,
        required=False,
        help_text="Pick the subscriber by external_ref instead of id",
    )
    subscriber_name = serializers.CharField(source="subscriber.customer_name", read_only=True)
    subscriber_mobile = serializers.CharField(source="subscriber.mobile_number", read_only=True)
    subscriber_external_ref = serializers.CharField(
        source="subscriber.external_ref", read_only=True
    )
    area_name = serializers.CharField(source="subscriber.area.name", read_only=True, default=None)
    schedule_type = serializers.SlugRelatedField(
        slug_field="key", queryset=ScheduleType.objects.all(), required=False
    )
    job = serializers.SerializerMethodField()
    follow_up = serializers.SerializerMethodField()

    class Meta:
        model = Schedule
        fields = [
            "id",
            "booking_reference",
            "subscriber",
            "subscriber_ref",
            "subscriber_name",
            "subscriber_mobile",
            "subscriber_external_ref",
            "area_name",
            "schedule_type",
            "booking_date",
            "scheduled_date",
            "status",
            "outcome",
            "new_date_requested",
            "remarks",
            "custom_fields",
            "cancelled_at",
            "cancel_reason",
            "job",
            "follow_up",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "outcome",
            "new_date_requested",
            "cancelled_at",
            "cancel_reason",
            "created_at",
            "updated_at",
        ]

    @property
    def tenant(self):
        return self.context["tenant"]

    @extend_schema_field(JobSummarySerializer(allow_null=True))
    def get_job(self, schedule):
        jobs = [j for j in schedule.jobs.all() if j.status != Job.Status.CANCELLED]
        return JobSummarySerializer(jobs[0]).data if jobs else None

    @extend_schema_field(FollowUpSummarySerializer(allow_null=True))
    def get_follow_up(self, schedule):
        items = [f for f in schedule.follow_ups.all() if f.status != FollowUpItem.Status.RESOLVED]
        return FollowUpSummarySerializer(items[0]).data if items else None

    def validate_booking_reference(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("This field is required."))
        clash = Schedule.objects.filter(tenant_id=self.tenant.pk, booking_reference=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("Another booking already uses this reference."))
        return value

    def validate_booking_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError(_("The booking date can't be in the future."))
        return value

    def validate_scheduled_date(self, value):
        unchanged = self.instance is not None and self.instance.scheduled_date == value
        if value and value < timezone.localdate() and not unchanged:
            raise serializers.ValidationError(_("The delivery date can't be in the past."))
        return value

    def validate_schedule_type(self, schedule_type):
        template_id = self.tenant.industry_template_id
        if schedule_type.template_id != template_id or not schedule_type.is_active:
            raise serializers.ValidationError(_("Not a schedule type of your industry template."))
        return schedule_type

    def validate(self, attrs):
        if self.instance is None:
            ref = attrs.pop("subscriber_ref", "").strip()
            if ref and not attrs.get("subscriber"):
                attrs["subscriber"] = Subscriber.objects.filter(
                    tenant_id=self.tenant.pk, external_ref=ref
                ).first()
                if attrs["subscriber"] is None:
                    raise serializers.ValidationError(
                        {"subscriber_ref": [_("No subscriber has this reference.")]}
                    )
            if not attrs.get("subscriber"):
                raise serializers.ValidationError({"subscriber": [_("This field is required.")]})
            if "booking_date" not in attrs:
                raise serializers.ValidationError({"booking_date": [_("This field is required.")]})
            if "schedule_type" not in attrs:
                attrs["schedule_type"] = (
                    ScheduleType.objects.filter(
                        template_id=self.tenant.industry_template_id, is_active=True
                    )
                    .order_by("order", "key")
                    .first()
                )
            attrs["custom_fields"] = clean_schedule_custom_fields(
                self.tenant, attrs["schedule_type"].key, attrs.get("custom_fields")
            )
        else:
            for locked in ("subscriber", "subscriber_ref", "schedule_type"):
                attrs.pop(locked, None)
            if "custom_fields" in attrs:
                attrs["custom_fields"] = clean_schedule_custom_fields(
                    self.tenant,
                    self.instance.schedule_type.key,
                    attrs["custom_fields"],
                    existing=self.instance.custom_fields,
                    partial=True,
                )
        return attrs


class SetDateSerializer(serializers.Serializer):
    scheduled_date = serializers.DateField()


class BulkSetDateSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=500)
    scheduled_date = serializers.DateField()


class CancelReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5, max_length=500, trim_whitespace=True)


class BulkSetDateResultSerializer(serializers.Serializer):
    updated = serializers.ListField(child=serializers.UUIDField())
    skipped_cancelled = serializers.ListField(child=serializers.UUIDField())
    not_found = serializers.ListField(child=serializers.UUIDField())


class TimelineEntrySerializer(serializers.Serializer):
    at = serializers.DateTimeField()
    kind = serializers.CharField()
    data = serializers.DictField()
    actor_email = serializers.CharField(allow_blank=True)
