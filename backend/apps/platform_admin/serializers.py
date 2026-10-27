import zoneinfo

from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.accounts.models import Membership
from apps.audit.models import AuditLog
from apps.core.models import Tenant
from apps.core.validators import (
    check_gstin_state,
    normalize_gstin,
    normalize_indian_mobile,
    validate_pin_code,
)

from .models import SupportSession

TENANT_FIELDS = [
    "name",
    "legal_name",
    "gstin",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "pin_code",
    "contact_name",
    "contact_phone",
    "contact_email",
    "default_language",
    "timezone",
    "daily_send_cap",
]


class TenantSerializer(serializers.ModelSerializer):
    """Client details and settings (spec 2.4 / 2.5). Validation matches the create form."""

    name = serializers.CharField(min_length=2, max_length=100)
    legal_name = serializers.CharField(min_length=2, max_length=200)
    gstin = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=15)
    address_line1 = serializers.CharField(max_length=200)
    address_line2 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100)
    pin_code = serializers.CharField()
    contact_name = serializers.CharField(max_length=100)
    contact_phone = serializers.CharField()
    daily_send_cap = serializers.IntegerField(min_value=1, max_value=100_000, default=5000)
    owner_email = serializers.EmailField(read_only=True, allow_null=True)

    class Meta:
        model = Tenant
        fields = [
            "id",
            *TENANT_FIELDS,
            "status",
            "suspended_at",
            "suspension_reason",
            "owner_email",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["status", "suspended_at", "suspension_reason"]

    def validate_gstin(self, value):
        gstin = normalize_gstin(value)
        if gstin:
            others = Tenant.objects.filter(gstin=gstin)
            if self.instance is not None:
                others = others.exclude(pk=self.instance.pk)
            if others.exists():
                raise serializers.ValidationError(_("Another client already uses this GSTIN."))
        return gstin

    def validate_pin_code(self, value):
        return validate_pin_code(value)

    def validate_contact_phone(self, value):
        return normalize_indian_mobile(value)

    def validate_timezone(self, value):
        if value not in zoneinfo.available_timezones():
            raise serializers.ValidationError(_("Unknown time zone."))
        return value

    def validate(self, attrs):
        state = attrs.get("state", getattr(self.instance, "state", None))
        gstin = attrs["gstin"] if "gstin" in attrs else getattr(self.instance, "gstin", None)
        check_gstin_state(gstin, state)
        return attrs


class TenantCreateSerializer(TenantSerializer):
    """Create client form (spec 2.4): client details plus the owner who is invited."""

    owner_name = serializers.CharField(max_length=100, write_only=True)
    owner_email = serializers.EmailField(write_only=True)
    owner_phone = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta(TenantSerializer.Meta):
        fields = [*TenantSerializer.Meta.fields, "owner_name", "owner_phone"]

    def validate_owner_phone(self, value):
        return normalize_indian_mobile(value) if value else ""


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5, max_length=500)


class MembershipAdminSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email")
    full_name = serializers.CharField(source="user.full_name")
    last_login = serializers.DateTimeField(source="user.last_login", allow_null=True)
    otp_enabled = serializers.BooleanField()

    class Meta:
        model = Membership
        fields = ["id", "email", "full_name", "role", "status", "last_login", "otp_enabled"]


class SupportSessionSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.name")
    staff_email = serializers.EmailField(source="staff_user.email")
    seconds_left = serializers.SerializerMethodField()
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = SupportSession
        fields = [
            "id",
            "tenant_id",
            "tenant_name",
            "staff_email",
            "reason",
            "started_at",
            "expires_at",
            "ended_at",
            "end_reason",
            "is_active",
            "seconds_left",
        ]

    def get_seconds_left(self, obj) -> int:
        return obj.seconds_left()

    def get_is_active(self, obj) -> bool:
        return obj.is_active()


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "created_at",
            "tenant_id",
            "actor_id",
            "actor_email",
            "support_session_id",
            "action",
            "object_type",
            "object_id",
            "before",
            "after",
            "ip_address",
            "user_agent",
        ]
