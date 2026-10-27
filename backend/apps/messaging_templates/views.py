from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.accounts.permissions import IsPlatformStaff, IsSuperAdmin
from apps.core.exceptions import ApiError
from apps.core.storage import signed_url
from apps.industry.models import ScheduleType

from . import services
from .models import TemplateStatusEvent, VersionStatus, WhatsAppTemplate, WhatsAppTemplateVersion
from .serializers import (
    ManualSignoffSerializer,
    ReasonOptionalSerializer,
    TemplateCreateSerializer,
    TemplateDetailSerializer,
    TemplateSerializer,
    VersionEditSerializer,
    VersionSerializer,
)
from .validation import allowed_variables

VID = r"versions/(?P<vid>[0-9a-f-]+)"


@extend_schema(tags=["admin: whatsapp templates"])
class WhatsAppTemplateViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """WhatsApp template editor and approval workflow (spec 2.7, Workflow 3.4)."""

    super_admin_actions = {"manual_signoff", "submit", "make_live", "disable"}

    def get_permissions(self):
        if self.action in self.super_admin_actions:
            return [IsSuperAdmin()]
        return [IsPlatformStaff()]

    def get_serializer_class(self):
        return TemplateDetailSerializer if self.action == "retrieve" else TemplateSerializer

    def get_queryset(self):
        versions = WhatsAppTemplateVersion.objects.select_related("signed_off_by").prefetch_related(
            Prefetch("events", queryset=TemplateStatusEvent.objects.select_related("actor"))
        )
        queryset = WhatsAppTemplate.objects.select_related(
            "tenant", "schedule_type"
        ).prefetch_related(Prefetch("versions", queryset=versions))
        params = self.request.query_params
        if tenant := params.get("tenant"):
            queryset = queryset.filter(tenant_id=tenant)
        if language := params.get("language"):
            queryset = queryset.filter(language=language)
        if status_filter := params.get("status"):
            queryset = queryset.filter(versions__status=status_filter).distinct()
        if search := params.get("search", "").strip():
            queryset = queryset.filter(name__icontains=search)
        return queryset.order_by("tenant__name", "name", "language")

    def _version(self, pk, vid):
        template = self.get_object()
        return get_object_or_404(WhatsAppTemplateVersion, pk=vid, template=template)

    def _detail(self, template_id, status_code=status.HTTP_200_OK):
        template = self.get_queryset().get(pk=template_id)
        return Response(TemplateDetailSerializer(template).data, status=status_code)

    @extend_schema(
        parameters=[
            OpenApiParameter("tenant", str),
            OpenApiParameter("language", str),
            OpenApiParameter("status", str, enum=VersionStatus.values),
            OpenApiParameter("search", str),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=TemplateCreateSerializer, responses={201: TemplateDetailSerializer})
    def create(self, request):
        serializer = TemplateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        version = services.create_template(data.pop("tenant"), data, request.user)
        return self._detail(version.template_id, status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[OpenApiParameter("schedule_type", str, required=True)],
        responses=inline_serializer(
            "TemplateVariables", {"variables": serializers.ListField(child=serializers.CharField())}
        ),
    )
    @action(detail=False, methods=["get"])
    def variables(self, request):
        """Variables the editor's picker may offer: the schedule type's fields + agency_name."""
        schedule_type = get_object_or_404(
            ScheduleType, pk=request.query_params.get("schedule_type")
        )
        return Response({"variables": sorted(allowed_variables(schedule_type))})

    @extend_schema(methods=["GET"], responses=VersionSerializer)
    @extend_schema(methods=["PATCH"], request=VersionEditSerializer, responses=VersionSerializer)
    @action(detail=True, methods=["get", "patch"], url_path=VID)
    def version(self, request, pk=None, vid=None):
        version = self._version(pk, vid)
        if request.method == "PATCH":
            serializer = VersionEditSerializer(data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            version = services.edit_version(version, serializer.validated_data, request.user)
        return Response(VersionSerializer(self._fresh(version)).data)

    def _fresh(self, version):
        return (
            WhatsAppTemplateVersion.objects.select_related("signed_off_by")
            .prefetch_related("events__actor")
            .get(pk=version.pk)
        )

    def _act(self, pk, vid, func, *args):
        version = func(self._version(pk, vid), self.request.user, *args)
        return Response(VersionSerializer(self._fresh(version)).data)

    @extend_schema(request=None, responses=VersionSerializer)
    @action(detail=True, methods=["post"], url_path=f"{VID}/send-to-client")
    def send_to_client(self, request, pk=None, vid=None):
        return self._act(pk, vid, services.send_to_client)

    @extend_schema(request=ManualSignoffSerializer, responses=VersionSerializer)
    @action(
        detail=True,
        methods=["post"],
        url_path=f"{VID}/manual-signoff",
        parser_classes=[MultiPartParser, FormParser],
    )
    def manual_signoff(self, request, pk=None, vid=None):
        serializer = ManualSignoffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return self._act(pk, vid, services.manual_signoff, data["evidence"], data["note"])

    @extend_schema(request=None, responses=VersionSerializer)
    @action(detail=True, methods=["post"], url_path=f"{VID}/submit")
    def submit(self, request, pk=None, vid=None):
        return self._act(pk, vid, services.submit)

    @extend_schema(request=None, responses=VersionSerializer)
    @action(detail=True, methods=["post"], url_path=f"{VID}/make-live")
    def make_live(self, request, pk=None, vid=None):
        return self._act(pk, vid, services.make_live)

    @extend_schema(request=ReasonOptionalSerializer, responses=VersionSerializer)
    @action(detail=True, methods=["post"], url_path=f"{VID}/disable")
    def disable(self, request, pk=None, vid=None):
        serializer = ReasonOptionalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._act(pk, vid, services.disable, serializer.validated_data["reason"])

    @extend_schema(request=None, responses={201: VersionSerializer})
    @action(detail=True, methods=["post"], url_path=f"{VID}/new-version")
    def new_version(self, request, pk=None, vid=None):
        draft = services.new_version(self._version(pk, vid), request.user)
        return Response(VersionSerializer(self._fresh(draft)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=VersionSerializer)
    @action(detail=True, methods=["post"], url_path=f"{VID}/sync-status")
    def sync_status(self, request, pk=None, vid=None):
        """Ask the provider for the review status now (Celery Beat also does this every 10 min)."""
        version = services.sync_status(self._version(pk, vid))
        return Response(VersionSerializer(self._fresh(version)).data)

    @extend_schema(responses=inline_serializer("EvidenceLink", {"url": serializers.URLField()}))
    @action(detail=True, methods=["get"], url_path=f"{VID}/evidence")
    def evidence(self, request, pk=None, vid=None):
        """A signed link to the sign-off evidence, valid for 5 minutes."""
        version = self._version(pk, vid)
        if not version.signoff_evidence:
            raise ApiError("no_evidence", "This version has no sign-off evidence.", 404)
        return Response({"url": signed_url(version.signoff_evidence.name)})
