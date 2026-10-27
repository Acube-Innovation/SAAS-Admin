"""
Development-only controls to play Meta and the client while the WhatsApp provider is the stub and
the client portal (Step 18) doesn't exist yet. Enabled only when all of these hold: DEBUG,
settings.WHATSAPP_DEV_CONTROLS, and the stub provider. Otherwise the endpoint answers 404.
"""

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership, TenantRole
from apps.accounts.permissions import IsSuperAdmin
from apps.core.exceptions import ApiError
from apps.core.tenancy import tenant_context

from . import providers, services
from .models import WhatsAppTemplateVersion
from .serializers import VersionSerializer

PROVIDER_STATUSES = {
    "provider_approve": providers.APPROVED,
    "provider_reject": providers.REJECTED,
    "provider_pause": providers.PAUSED,
}


def dev_controls_enabled() -> bool:
    return bool(
        settings.DEBUG
        and settings.WHATSAPP_DEV_CONTROLS
        and settings.WHATSAPP_PROVIDER.endswith(".StubProvider")
    )


class SimulateSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=[*PROVIDER_STATUSES, "client_approve", "client_send_back"]
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")


class SimulateView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(request=SimulateSerializer, responses=VersionSerializer, tags=["admin: dev"])
    def post(self, request, vid):
        """Development only: act as Meta (then sync) or as the client's owner on a version."""
        if not dev_controls_enabled():
            raise Http404
        serializer = SimulateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action, reason = serializer.validated_data["action"], serializer.validated_data["reason"]
        version = get_object_or_404(WhatsAppTemplateVersion, pk=vid)

        if action in PROVIDER_STATUSES:
            if not version.provider_template_id:
                raise ApiError("not_submitted", "Submit the version to the provider first.", 409)
            providers.StubProvider.set_status(
                version.provider_template_id, PROVIDER_STATUSES[action], reason
            )
            version = services.sync_status(version)
        else:
            with tenant_context(version.tenant_id):
                membership = (
                    Membership.objects.filter(role__in=[TenantRole.OWNER, TenantRole.MANAGER])
                    .select_related("user")
                    .order_by("role", "created_at")
                    .first()
                )
            if membership is None:
                raise ApiError("no_client_user", "The client has no owner or manager.", 409)
            if action == "client_approve":
                version = services.client_approve(version, membership.user)
            else:
                version = services.client_send_back(
                    version, membership.user, reason or "Please change the wording."
                )
        return Response(VersionSerializer(version).data)
