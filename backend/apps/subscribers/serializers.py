from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.core.validators import normalize_indian_mobile
from apps.industry.fields import clean_custom_fields, custom_field_definitions
from apps.jobs.models import StaffMember

from .models import Area, ImportJob, Subscriber


class TenantScopedPK(serializers.PrimaryKeyRelatedField):
    """A foreign key limited to the request's tenant (RLS does this too; this gives a 400)."""

    def get_queryset(self):
        return super().get_queryset().filter(tenant_id=self.context["request"].tenant_id)


class AreaSerializer(serializers.ModelSerializer):
    default_staff = TenantScopedPK(
        queryset=StaffMember.objects.all(), allow_null=True, required=False
    )
    default_staff_name = serializers.CharField(
        source="default_staff.name", read_only=True, default=None
    )
    subscriber_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Area
        fields = [
            "id",
            "name",
            "code",
            "default_staff",
            "default_staff_name",
            "is_active",
            "subscriber_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError(_("At least 2 characters."))
        clash = Area.objects.filter(tenant_id=self.context["request"].tenant_id, name__iexact=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("An area with this name already exists."))
        return value

    def validate_code(self, value):
        return value.strip()

    def validate_default_staff(self, staff):
        unchanged = self.instance is not None and self.instance.default_staff_id == getattr(
            staff, "pk", None
        )
        if staff is not None and not staff.is_active and not unchanged:
            raise serializers.ValidationError(_("This staff member is inactive."))
        return staff


class SubscriberSerializer(serializers.ModelSerializer):
    area = TenantScopedPK(queryset=Area.objects.all(), allow_null=True, required=False)
    area_name = serializers.CharField(source="area.name", read_only=True, default=None)
    can_be_messaged = serializers.BooleanField(read_only=True)
    preferred_language = serializers.ChoiceField(
        choices=Subscriber._meta.get_field("preferred_language").choices, required=False
    )

    class Meta:
        model = Subscriber
        fields = [
            "id",
            "customer_name",
            "mobile_number",
            "alternate_number",
            "external_ref",
            "preferred_language",
            "address",
            "area",
            "area_name",
            "whatsapp_consent",
            "consent_date",
            "consent_source",
            "opted_out",
            "opted_out_at",
            "status",
            "custom_fields",
            "can_be_messaged",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "opted_out", "opted_out_at", "created_at", "updated_at"]
        extra_kwargs = {"whatsapp_consent": {"required": True}}

    @property
    def tenant_id(self):
        return self.context["request"].tenant_id

    def validate_customer_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("This field is required."))
        return value

    def validate_mobile_number(self, value):
        return normalize_indian_mobile(value)

    def validate_alternate_number(self, value):
        return normalize_indian_mobile(value) if value.strip() else ""

    def validate_external_ref(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("This field is required."))
        clash = Subscriber.objects.filter(tenant_id=self.tenant_id, external_ref=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("Another subscriber already uses this reference."))
        return value

    def validate_area(self, area):
        unchanged = self.instance is not None and self.instance.area_id == getattr(area, "pk", None)
        if area is not None and not area.is_active and not unchanged:
            raise serializers.ValidationError(_("This area is inactive."))
        return area

    def validate_consent_date(self, value):
        if value and value > timezone.localdate():
            raise serializers.ValidationError(_("Can't be in the future."))
        return value

    def validate(self, attrs):
        def current(name):
            if name in attrs:
                return attrs[name]
            return getattr(self.instance, name) if self.instance else None

        if current("whatsapp_consent"):
            errors = {}
            if not current("consent_date"):
                errors["consent_date"] = [_("Required when the customer has given consent.")]
            if not current("consent_source"):
                errors["consent_source"] = [_("Required when the customer has given consent.")]
            if errors:
                raise serializers.ValidationError(errors)
        elif "whatsapp_consent" in attrs:
            attrs["consent_date"], attrs["consent_source"] = None, ""

        if self.instance is None:
            attrs.setdefault("preferred_language", self.context["tenant"].default_language)

        if "custom_fields" in attrs or self.instance is None:
            definitions = custom_field_definitions(self.context["tenant"], "subscriber")
            try:
                attrs["custom_fields"] = clean_custom_fields(
                    definitions,
                    attrs.get("custom_fields"),
                    existing=self.instance.custom_fields if self.instance else None,
                    partial=self.instance is not None,
                )
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({"custom_fields": exc.detail}) from None
        return attrs


class ImportJobSerializer(serializers.ModelSerializer):
    created_by_email = serializers.CharField(source="created_by.email", read_only=True, default="")
    has_error_file = serializers.SerializerMethodField()

    class Meta:
        model = ImportJob
        fields = [
            "id",
            "entity",
            "original_filename",
            "status",
            "total_rows",
            "valid_rows",
            "error_rows",
            "created_rows",
            "updated_rows",
            "errors",
            "summary",
            "has_error_file",
            "failure_reason",
            "created_by_email",
            "created_at",
            "previewed_at",
            "confirmed_at",
            "finished_at",
        ]
        read_only_fields = fields

    def get_has_error_file(self, job) -> bool:
        return bool(job.error_file)


class ImportUploadSerializer(serializers.Serializer):
    entity = serializers.ChoiceField(choices=["subscribers", "bookings"])
    file = serializers.FileField()
