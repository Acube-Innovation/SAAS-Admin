from django.contrib.auth import password_validation
from rest_framework import serializers

from .models import Membership, User


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)


class OtpCodeSerializer(serializers.Serializer):
    otp_code = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Enter 6 digits."})


class OtpSetupSerializer(serializers.Serializer):
    otpauth_url = serializers.CharField(help_text="Show as a QR code in the authenticator app.")
    secret = serializers.CharField(help_text="Hex key, for manual entry.")


class SelectTenantSerializer(serializers.Serializer):
    tenant_id = serializers.UUIDField()


class AcceptInviteSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False)
    full_name = serializers.CharField(max_length=100, required=False, allow_blank=True)


class MembershipSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.name")
    tenant_status = serializers.CharField(source="tenant.status")

    class Meta:
        model = Membership
        fields = ["id", "tenant_id", "tenant_name", "tenant_status", "role", "status"]


class AuthStateSerializer(serializers.Serializer):
    """What the frontend needs to route a user: who they are and which 2FA step is next."""

    id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name = serializers.CharField()
    platform_role = serializers.CharField(allow_blank=True)
    requires_2fa = serializers.BooleanField()
    otp_device_confirmed = serializers.BooleanField()
    otp_verified = serializers.BooleanField()
    otp_required = serializers.BooleanField(help_text="Enter a code from the authenticator app.")
    otp_setup_required = serializers.BooleanField(help_text="Set up an authenticator app first.")
    active_tenant_id = serializers.UUIDField(allow_null=True)
    memberships = MembershipSerializer(many=True)


class InviteInfoSerializer(serializers.Serializer):
    email = serializers.EmailField()
    tenant_name = serializers.CharField()
    role = serializers.CharField()
    expires_at = serializers.DateTimeField()
    needs_new_password = serializers.BooleanField()
    requires_2fa = serializers.BooleanField()


def validate_new_password(password, user: User):
    password_validation.validate_password(password, user)
