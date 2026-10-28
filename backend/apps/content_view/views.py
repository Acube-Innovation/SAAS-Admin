"""
Messages and voice (portal spec 2.6): the agency's WhatsApp templates, voice scripts and question
flows, read-only. The only content actions a client has are Approve and Send back on a version
sent to them (Workflow 3.3). There is deliberately no create, edit or delete route.
"""

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import tenant_roles
from apps.core.models import Tenant
from apps.core.portal import PortalViewMixin
from apps.industry.services import tenant_config
from apps.messaging_templates import services
from apps.messaging_templates.models import (
    TemplateStatusEvent,
    VoiceScript,
    VoiceScriptVersion,
    WhatsAppTemplate,
    WhatsAppTemplateVersion,
)

from .serializers import (
    CLIENT_VISIBLE,
    ClientTemplateSerializer,
    ClientVersionSerializer,
    ClientVoiceScriptSerializer,
    QuestionFlowSerializer,
    SendBackSerializer,
)

ALL_ROLES = tenant_roles("owner", "manager", "office_staff")
REVIEWERS = tenant_roles("owner", "manager")
VID = r"versions/(?P<vid>[0-9a-f-]+)"


@extend_schema(tags=["portal: messages and voice"])
class ContentTemplateViewSet(
    PortalViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """WhatsApp templates with the live version, the version awaiting review and the history."""

    serializer_class = ClientTemplateSerializer
    pagination_class = None

    def get_permissions(self):
        if self.action in ("approve", "send_back"):
            return [REVIEWERS()]
        return [ALL_ROLES()]

    def get_queryset(self):
        events = TemplateStatusEvent.objects.select_related("actor")
        versions = WhatsAppTemplateVersion.objects.select_related("signed_off_by").prefetch_related(
            Prefetch("events", queryset=events)
        )
        queryset = self.scoped(WhatsAppTemplate.objects.select_related("schedule_type"))
        # Templates that have reached the client: a live version or one sent for review.
        visible = self.scoped(WhatsAppTemplateVersion.objects.all()).filter(
            is_live=True
        ) | self.scoped(WhatsAppTemplateVersion.objects.filter(status__in=CLIENT_VISIBLE))
        return (
            queryset.filter(pk__in=visible.values("template_id"))
            .prefetch_related(Prefetch("versions", queryset=versions))
            .order_by("name", "language")
        )

    def _version(self, pk, vid):
        template = self.get_object()
        return get_object_or_404(
            WhatsAppTemplateVersion, pk=vid, template=template, tenant_id=self.request.tenant_id
        )

    def _respond(self, version):
        fresh = (
            WhatsAppTemplateVersion.objects.select_related("signed_off_by")
            .prefetch_related("events")
            .get(pk=version.pk)
        )
        return Response(ClientVersionSerializer(fresh).data)

    @extend_schema(request=None, responses=ClientVersionSerializer)
    @action(detail=True, methods=["post"], url_path=f"{VID}/approve")
    def approve(self, request, pk=None, vid=None):
        """Workflow 3.3: the owner or manager approves a version sent to them (-> signed off)."""
        return self._respond(services.client_approve(self._version(pk, vid), request.user))

    @extend_schema(request=SendBackSerializer, responses=ClientVersionSerializer)
    @action(detail=True, methods=["post"], url_path=f"{VID}/send-back")
    def send_back(self, request, pk=None, vid=None):
        """Workflow 3.3: send the version back to platform staff with a comment."""
        serializer = SendBackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = self._version(pk, vid)
        return self._respond(
            services.client_send_back(version, request.user, serializer.validated_data["comment"])
        )


@extend_schema(tags=["portal: messages and voice"])
class ContentVoiceScriptViewSet(
    PortalViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """AI voice call scripts (live versions), read-only."""

    serializer_class = ClientVoiceScriptSerializer
    permission_classes = [ALL_ROLES]
    pagination_class = None

    def get_queryset(self):
        live = VoiceScriptVersion.objects.filter(status="live")
        return (
            self.scoped(VoiceScript.objects.select_related("schedule_type"))
            .filter(pk__in=live.values("script_id"))
            .prefetch_related(Prefetch("versions", queryset=live))
            .order_by("name", "language")
        )


class QuestionFlowView(APIView):
    """The agency's question flows: each answer and the outcome it records."""

    permission_classes = [ALL_ROLES]

    @extend_schema(tags=["portal: messages and voice"], responses=QuestionFlowSerializer(many=True))
    def get(self, request):
        tenant = Tenant.objects.select_related("industry_template").get(pk=request.tenant_id)
        flows = [
            {
                "schedule_type": st["key"],
                "name": st["name"],
                "answers": (st.get("question_flow") or {}).get("answers", []),
            }
            for st in tenant_config(tenant)["schedule_types"]
        ]
        return Response(QuestionFlowSerializer(flows, many=True).data)
