from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import tenant_roles
from apps.audit.models import Action
from apps.audit.services import write_audit
from apps.core.models import Tenant
from apps.industry.services import tenant_config

from . import services

ALL_ROLES = tenant_roles("owner", "manager", "office_staff")
OWNER = tenant_roles("owner")
# The agency roles plus "support" (platform staff in a support session).
PORTAL_ROLES = ("owner", "manager", "office_staff", "support")


class PortalConfigSerializer(serializers.Serializer):
    tenant_id = serializers.UUIDField()
    tenant_name = serializers.CharField()
    tenant_status = serializers.ChoiceField(choices=Tenant.Status.choices)
    suspended_at = serializers.DateTimeField(allow_null=True)
    suspension_reason = serializers.CharField(allow_blank=True)
    role = serializers.ChoiceField(choices=PORTAL_ROLES)
    default_language = serializers.CharField()
    template = serializers.CharField()
    subscriber_fields = serializers.ListField(child=serializers.DictField())
    schedule_types = serializers.ListField(child=serializers.DictField())


class PortalConfigView(APIView):
    """
    Everything the portal shell needs: the user's role here (menu, spec 1.1), the agency's status
    (read-only banner) and the industry template's labels and choice lists.
    """

    permission_classes = [ALL_ROLES]

    @extend_schema(tags=["portal"], responses=PortalConfigSerializer)
    def get(self, request):
        tenant = Tenant.objects.select_related("industry_template").get(pk=request.tenant_id)
        data = {
            "tenant_id": tenant.pk,
            "tenant_name": tenant.name,
            "tenant_status": tenant.status,
            "suspended_at": tenant.suspended_at,
            "suspension_reason": tenant.suspension_reason,
            "role": services.current_role(request),
            "default_language": tenant.default_language,
            **tenant_config(tenant),
        }
        return Response(PortalConfigSerializer(data).data)


class ChecklistItemSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=services.CHECKLIST)
    done = serializers.BooleanField()
    skipped = serializers.BooleanField()
    can_skip = serializers.BooleanField()
    manual = serializers.BooleanField(help_text="Ticked by the owner (the others tick themselves)")


class ChecklistSerializer(serializers.Serializer):
    items = ChecklistItemSerializer(many=True)
    complete = serializers.BooleanField()


class HomeSerializer(serializers.Serializer):
    date = serializers.DateField()
    tomorrow = serializers.DateField()
    tomorrow_counts = inline_serializer(
        "HomeTomorrowCounts",
        {
            k: serializers.IntegerField()
            for k in [
                "total",
                "confirmed",
                "reschedule",
                "not_available",
                "no_response",
                "awaiting_reply",
                "needs_review",
                "not_reminded_yet",
            ]
        },
    )
    follow_up = inline_serializer(
        "HomeFollowUp",
        {
            "open": serializers.IntegerField(),
            "oldest_minutes": serializers.IntegerField(allow_null=True),
            "by_ageing": serializers.DictField(child=serializers.IntegerField()),
        },
    )
    no_date = serializers.IntegerField()
    alerts = inline_serializer(
        "HomeAlert",
        {
            "code": serializers.ChoiceField(
                choices=[
                    "tenant_suspended",
                    "template_paused",
                    "no_live_template",
                    "send_cap_reached",
                ]
            ),
            "severity": serializers.ChoiceField(choices=["error", "warn"]),
        },
        many=True,
    )
    sent_today = serializers.IntegerField()
    daily_send_cap = serializers.IntegerField()
    checklist = ChecklistSerializer(allow_null=True, help_text="Owner only (Workflow 3.6)")
    generated_at = serializers.DateTimeField()


class HomeView(APIView):
    """Home (spec 2.1): tomorrow's bookings, follow-up queue, alerts. Refreshed every 30 s."""

    permission_classes = [ALL_ROLES]

    @extend_schema(tags=["portal"], responses=HomeSerializer)
    def get(self, request):
        tenant = Tenant.objects.get(pk=request.tenant_id)
        return Response(HomeSerializer(services.home(tenant, services.current_role(request))).data)


class ChecklistActionSerializer(serializers.Serializer):
    item = serializers.ChoiceField(choices=sorted(services.MANUAL))
    action = serializers.ChoiceField(choices=["done", "skip", "unskip"])

    def validate(self, attrs):
        if attrs["action"] != "done" and attrs["item"] not in services.SKIPPABLE:
            raise serializers.ValidationError({"action": ["This item can't be skipped."]})
        return attrs


class ChecklistView(APIView):
    """The owner ticks a manual checklist item or skips auto top-up (Workflow 3.6)."""

    permission_classes = [OWNER]

    @extend_schema(
        tags=["portal"], request=ChecklistActionSerializer, responses=ChecklistSerializer
    )
    def post(self, request):
        data = ChecklistActionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        tenant = Tenant.objects.get(pk=request.tenant_id)
        before = (tenant.settings or {}).get("onboarding", {})
        result = services.mark_checklist(
            tenant, data.validated_data["item"], data.validated_data["action"]
        )
        write_audit(
            Action.UPDATE,
            tenant,
            before={"onboarding": before},
            after={"onboarding": tenant.settings.get("onboarding", {})},
            tenant=tenant,
        )
        return Response(ChecklistSerializer(result).data)
