"""Dashboard tiles (spec 2.2). Message and webhook tiles read zero until the Phase 2 engine."""

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsPlatformStaff
from apps.core.models import Tenant
from apps.messaging_templates.models import VersionStatus, WhatsAppTemplateVersion
from apps.messaging_templates.providers import get_provider

from .models import SupportSession

AWAITING_ACTION = [VersionStatus.SIGNED_OFF, VersionStatus.SENT_BACK, VersionStatus.REJECTED]


class MessagesTodaySerializer(serializers.Serializer):
    sent = serializers.IntegerField()
    delivered = serializers.IntegerField()
    failed = serializers.IntegerField()
    replied = serializers.IntegerField()


class DashboardSerializer(serializers.Serializer):
    clients_active = serializers.IntegerField()
    clients_suspended = serializers.IntegerField()
    messages_today = MessagesTodaySerializer()
    messages_live = serializers.BooleanField(help_text="False until the Phase 2 reminder engine")
    quality_rating = serializers.ChoiceField(choices=["GREEN", "YELLOW", "RED", "UNKNOWN"])
    quality_alert = serializers.BooleanField()
    failed_webhooks_24h = serializers.IntegerField()
    templates_awaiting_action = serializers.IntegerField()
    open_change_requests = serializers.IntegerField()
    active_support_sessions = serializers.IntegerField()
    generated_at = serializers.DateTimeField()


def quality_rating() -> str:
    try:
        rating = get_provider().get_quality_rating().upper()
    except Exception:
        return "UNKNOWN"
    return rating if rating in {"GREEN", "YELLOW", "RED"} else "UNKNOWN"


class DashboardView(APIView):
    permission_classes = [IsPlatformStaff]

    @extend_schema(responses=DashboardSerializer, tags=["admin: dashboard"])
    def get(self, request):
        now = timezone.now()
        rating = quality_rating()
        data = {
            "clients_active": Tenant.objects.filter(status=Tenant.Status.ACTIVE).count(),
            "clients_suspended": Tenant.objects.filter(status=Tenant.Status.SUSPENDED).count(),
            # Phase 2 (reminder engine, webhook ingest) fills these from attempts and responses.
            "messages_today": {"sent": 0, "delivered": 0, "failed": 0, "replied": 0},
            "messages_live": False,
            "quality_rating": rating,
            "quality_alert": rating == "RED",
            "failed_webhooks_24h": 0,
            "templates_awaiting_action": WhatsAppTemplateVersion.objects.filter(
                status__in=AWAITING_ACTION
            ).count(),
            # Change requests arrive with Step A1.
            "open_change_requests": 0,
            "active_support_sessions": SupportSession.objects.filter(
                ended_at=None, expires_at__gt=now
            ).count(),
            "generated_at": now,
        }
        return Response(DashboardSerializer(data).data)
