import re

from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils.text import slugify
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import tenant_roles
from apps.audit.mixins import AuditedModelMixin
from apps.audit.models import Action
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.portal import PortalViewMixin
from apps.industry.services import tenant_config

from . import importer
from .models import Area, ImportJob, Subscriber
from .serializers import (
    AreaSerializer,
    ImportJobSerializer,
    ImportUploadSerializer,
    SubscriberSerializer,
)

ALL_ROLES = tenant_roles("owner", "manager", "office_staff")
MANAGERS = tenant_roles("owner", "manager")


class PortalConfigView(APIView):
    """The tenant's industry configuration: field labels, choice lists, schedule types."""

    permission_classes = [ALL_ROLES]

    @extend_schema(
        tags=["portal"],
        responses=inline_serializer(
            "PortalConfig",
            {
                "tenant_name": serializers.CharField(),
                "default_language": serializers.CharField(),
                "template": serializers.CharField(),
                "subscriber_fields": serializers.ListField(child=serializers.DictField()),
                "schedule_types": serializers.ListField(child=serializers.DictField()),
            },
        ),
    )
    def get(self, request):
        tenant = Tenant.objects.select_related("industry_template").get(pk=request.tenant_id)
        return Response(
            {
                "tenant_name": tenant.name,
                "default_language": tenant.default_language,
                **tenant_config(tenant),
            }
        )


@extend_schema(tags=["portal: areas and staff"])
class AreaViewSet(AuditedModelMixin, PortalViewMixin, viewsets.ModelViewSet):
    """Areas / routes (spec 2.7). Everyone can list them (subscriber form); owner/manager edit."""

    serializer_class = AreaSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]
    audit_status_fields = ("is_active",)

    def get_permissions(self):
        return [(ALL_ROLES if self.request.method in ("GET", "HEAD", "OPTIONS") else MANAGERS)()]

    def get_queryset(self):
        queryset = (
            self.scoped(Area.objects.select_related("default_staff"))
            .annotate(subscriber_count=Count("subscribers"))
            .order_by("name")  # Meta.ordering is ignored on GROUP BY queries
        )
        if self.request.query_params.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        return queryset

    def perform_create(self, serializer):
        serializer.save(tenant_id=self.request.tenant_id)
        self.audit(
            Action.CREATE, serializer.instance, after=self.audit_snapshot(serializer.instance)
        )


class SubscriberPagination(PageNumberPagination):
    page_size = 50


SUBSCRIBER_FILTERS = [
    OpenApiParameter("search", str, description="Name, mobile or reference"),
    OpenApiParameter("area", str, description="Area id, or 'none'"),
    OpenApiParameter("status", str, enum=["active", "inactive"]),
    OpenApiParameter("consent", bool),
    OpenApiParameter("opted_out", bool),
]


@extend_schema(tags=["portal: subscribers"])
class SubscriberViewSet(AuditedModelMixin, PortalViewMixin, viewsets.ModelViewSet):
    """Subscribers (spec 2.2). No delete: deactivate instead."""

    serializer_class = SubscriberSerializer
    permission_classes = [ALL_ROLES]
    pagination_class = SubscriberPagination
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        queryset = self.scoped(Subscriber.objects.select_related("area"))
        params = self.request.query_params
        if search := params.get("search", "").strip():
            condition = Q(customer_name__icontains=search) | Q(external_ref__icontains=search)
            digits = re.sub(r"\D", "", search)
            if len(digits) >= 4:
                condition |= Q(mobile_number__contains=digits[-10:])
            queryset = queryset.filter(condition)
        if area := params.get("area"):
            queryset = (
                queryset.filter(area=None) if area == "none" else queryset.filter(area_id=area)
            )
        if status := params.get("status"):
            queryset = queryset.filter(status=status)
        for param, field in (("consent", "whatsapp_consent"), ("opted_out", "opted_out")):
            if params.get(param) in ("true", "false"):
                queryset = queryset.filter(**{field: params[param] == "true"})
        return queryset

    @extend_schema(parameters=SUBSCRIBER_FILTERS)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(tenant_id=self.request.tenant_id)
        self.audit(
            Action.CREATE, serializer.instance, after=self.audit_snapshot(serializer.instance)
        )

    @extend_schema(request=None, responses=SubscriberSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        subscriber = self.get_object()
        if subscriber.status != Subscriber.Status.INACTIVE:
            before = self.audit_snapshot(subscriber)
            subscriber.status = Subscriber.Status.INACTIVE
            subscriber.save(update_fields=["status", "updated_at"])
            self.audit(
                Action.STATUS_CHANGE,
                subscriber,
                before=before,
                after=self.audit_snapshot(subscriber),
            )
        return Response(self.get_serializer(subscriber).data)


@extend_schema(tags=["portal: imports"])
class ImportJobViewSet(
    PortalViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """CSV imports (Workflow 3.5): template, upload, preview, confirm, error file."""

    serializer_class = ImportJobSerializer
    permission_classes = [ALL_ROLES]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = self.scoped(ImportJob.objects.select_related("created_by"))
        if entity := self.request.query_params.get("entity"):
            queryset = queryset.filter(entity=entity)
        return queryset

    @extend_schema(request=ImportUploadSerializer, responses={201: ImportJobSerializer})
    def create(self, request):
        upload = ImportUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)
        tenant = Tenant.objects.get(pk=request.tenant_id)
        job = importer.start_import(
            tenant, upload.validated_data["entity"], upload.validated_data["file"], request.user
        )
        return Response(ImportJobSerializer(job).data, status=201)

    @extend_schema(request=None, responses=ImportJobSerializer)
    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        self.get_object()  # 404 for other tenants' jobs
        with transaction.atomic():
            job = importer.request_confirm(pk, request.user)
        return Response(ImportJobSerializer(job).data)

    @extend_schema(responses={(200, "text/csv"): str})
    @action(detail=True, methods=["get"], url_path="error-file")
    def error_file(self, request, pk=None):
        job = self.get_object()
        if not job.error_file:
            raise ApiError("not_found", "This import has no errors to download.", 404)
        with job.error_file.open("rb") as handle:
            content = handle.read()
        name = slugify(job.original_filename.rsplit(".", 1)[0]) or "import"
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}-errors.csv"'
        return response

    @extend_schema(
        parameters=[OpenApiParameter("entity", str, enum=["subscribers"], required=True)],
        responses={(200, "text/csv"): str},
    )
    @action(detail=False, methods=["get"])
    def template(self, request):
        entity = request.query_params.get("entity", "")
        tenant = Tenant.objects.select_related("industry_template").get(pk=request.tenant_id)
        content = importer.template_csv(tenant, entity)
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{entity}-template.csv"'
        return response
