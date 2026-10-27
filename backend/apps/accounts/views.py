from django.contrib.auth import login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django_otp import login as otp_login
from django_otp import user_has_device
from django_otp.plugins.otp_totp.models import TOTPDevice
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.context import refresh_actor
from apps.audit.models import Action
from apps.audit.services import write_audit
from apps.core.exceptions import ApiError

from . import services
from .models import TenantRole
from .permissions import otp_verified
from .serializers import (
    AcceptInviteSerializer,
    AuthStateSerializer,
    InviteInfoSerializer,
    LoginSerializer,
    OtpCodeSerializer,
    OtpSetupSerializer,
    SelectTenantSerializer,
)
from .tenancy import ACTIVE_TENANT_SESSION_KEY, active_tenant_ids

INVALID_OTP = ApiError("invalid_otp", "That code is not valid. Try again.", 400)


def auth_state(request):
    user = request.user
    requires_2fa = user.requires_2fa()
    confirmed = user_has_device(user, confirmed=True)
    verified = otp_verified(request)
    data = {
        "id": user.pk,
        "email": user.email,
        "full_name": user.full_name,
        "platform_role": user.platform_role,
        "requires_2fa": requires_2fa,
        "otp_device_confirmed": confirmed,
        "otp_verified": verified,
        "otp_required": confirmed and not verified,
        "otp_setup_required": requires_2fa and not confirmed,
        "active_tenant_id": getattr(request, "tenant_id", None),
        "memberships": user.memberships(),
    }
    return AuthStateSerializer(data).data


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """Sets the csrftoken cookie. The SPA sends it back in the X-CSRFToken header."""

    permission_classes = [AllowAny]

    @extend_schema(responses={204: None})
    def get(self, request):
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    """Step 1 of sign-in: email + password. The response says whether 2FA comes next."""

    permission_classes = [AllowAny]

    @extend_schema(request=LoginSerializer, responses=AuthStateSerializer)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.authenticate_with_lockout(**serializer.validated_data)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        refresh_actor(user)
        write_audit(Action.LOGIN, user, tenant=None)
        return Response(auth_state(request))


class LogoutView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        if request.user.is_authenticated:
            write_audit(Action.LOGOUT, request.user, tenant=None)
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """Current user and 2FA state. Available before 2FA so the frontend can route the user."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=AuthStateSerializer)
    def get(self, request):
        return Response(auth_state(request))


class OtpSetupView(APIView):
    """Start authenticator set-up: returns the otpauth:// URL (shown as a QR code) and secret."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=OtpSetupSerializer)
    def post(self, request):
        if user_has_device(request.user, confirmed=True):
            raise ApiError(
                "otp_already_set_up", "Two-factor authentication is already set up.", 409
            )
        TOTPDevice.objects.filter(user=request.user, confirmed=False).delete()
        device = TOTPDevice.objects.create(user=request.user, name="Authenticator", confirmed=False)
        write_audit(Action.OTP_SETUP_STARTED, request.user, tenant=None)
        return Response({"otpauth_url": device.config_url, "secret": device.key})


class OtpConfirmView(APIView):
    """Finish set-up with the first code from the app. Marks the session as 2FA-verified."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=OtpCodeSerializer, responses=AuthStateSerializer)
    def post(self, request):
        serializer = OtpCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
        if device is None:
            raise ApiError("otp_setup_missing", "Start two-factor set-up first.", 409)
        if not device.verify_token(serializer.validated_data["otp_code"]):
            write_audit(Action.OTP_FAILED, request.user, after={"step": "setup"}, tenant=None)
            raise INVALID_OTP
        device.confirmed = True
        device.save(update_fields=["confirmed"])
        otp_login(request, device)
        write_audit(Action.OTP_ENABLED, request.user, tenant=None)
        return Response(auth_state(request))


class OtpVerifyView(APIView):
    """Step 2 of sign-in for users with an authenticator: verify a 6-digit code."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=OtpCodeSerializer, responses=AuthStateSerializer)
    def post(self, request):
        serializer = OtpCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["otp_code"]
        for device in TOTPDevice.objects.filter(user=request.user, confirmed=True):
            # verify_token applies django-otp's throttling after repeated failures.
            if device.verify_token(code):
                otp_login(request, device)
                write_audit(Action.OTP_VERIFIED, request.user, tenant=None)
                return Response(auth_state(request))
        write_audit(Action.OTP_FAILED, request.user, after={"step": "sign_in"}, tenant=None)
        raise INVALID_OTP


class SelectTenantView(APIView):
    """Choose the active tenant for users with more than one membership."""

    @extend_schema(request=SelectTenantSerializer, responses=AuthStateSerializer)
    def post(self, request):
        serializer = SelectTenantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant_id = serializer.validated_data["tenant_id"]
        if tenant_id not in active_tenant_ids(request.user):
            raise ApiError("not_a_member", "You are not a member of that organisation.", 403)
        request.session[ACTIVE_TENANT_SESSION_KEY] = str(tenant_id)
        request.tenant_id = tenant_id
        return Response(auth_state(request))


class InviteView(APIView):
    """Check an invite link before showing the acceptance form."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(responses=InviteInfoSerializer)
    def get(self, request, token):
        invite = services.get_valid_invite(token)
        membership = invite.membership
        data = {
            "email": membership.user.email,
            "tenant_name": membership.tenant.name,
            "role": membership.role,
            "expires_at": invite.expires_at,
            "needs_new_password": not membership.user.has_usable_password(),
            "requires_2fa": membership.role == TenantRole.OWNER or membership.user.requires_2fa(),
        }
        return Response(InviteInfoSerializer(data).data)


@method_decorator(csrf_protect, name="dispatch")
class AcceptInviteView(APIView):
    """Accept an invite: set (or confirm) the password, activate the membership, sign in."""

    permission_classes = [AllowAny]

    @extend_schema(request=AcceptInviteSerializer, responses=AuthStateSerializer)
    def post(self, request, token):
        serializer = AcceptInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.accept_invite(token, **serializer.validated_data)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        refresh_actor(user)
        write_audit(Action.LOGIN, user, after={"via": "invite"}, tenant=None)
        return Response(auth_state(request))
