from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.accounts.models import User
from apps.accounts.permissions import IsPlatformStaff, tenant_roles
from apps.core.models import Tenant
from apps.core.portal import PortalViewMixin
from apps.messaging_templates.models import WhatsAppTemplateVersion

from . import services
from .models import ChangeRequest, ChangeRequestComment
from .serializers import (
    AdminChangeRequestDetailSerializer,
    AdminChangeRequestSerializer,
    AdminChangeRequestUpdateSerializer,
    CandidateVersionSerializer,
    ChangeRequestCreateSerializer,
    ChangeRequestDetailSerializer,
    ChangeRequestSerializer,
    CommentCreateSerializer,
    DeclineSerializer,
    LinkVersionSerializer,
    StaffUserSerializer,
    candidate_versions,
)

ALL_ROLES = tenant_roles("owner", "manager", "office_staff")
REQUESTERS = tenant_roles("owner", "manager")
STATUS_PARAM = OpenApiParameter("status", str, enum=ChangeRequest.Status.values)


class ChangeRequestPagination(PageNumberPagination):
    page_size = 50


def _base_queryset():
    comments = ChangeRequestComment.objects.select_related("author")
    return ChangeRequest.objects.select_related(
        "tenant", "requested_by", "assignee", "linked_version__template"
    ).prefetch_related(Prefetch("comments", queryset=comments))


@extend_schema(tags=["portal: messages and voice"])
class PortalChangeRequestViewSet(
    PortalViewMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """The agency's change requests (portal spec 2.6, Workflow 3.3). No edit or delete."""

    pagination_class = ChangeRequestPagination

    def get_permissions(self):
        if self.action in ("create", "comments"):
            return [REQUESTERS()]
        return [ALL_ROLES()]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ChangeRequestDetailSerializer
        return ChangeRequestSerializer

    def get_queryset(self):
        queryset = self.scoped(_base_queryset())
        if (value := self.request.query_params.get("status")) in ChangeRequest.Status.values:
            queryset = queryset.filter(status=value)
        return queryset.order_by("-created_at")

    def _detail(self, pk, code=status.HTTP_200_OK):
        return Response(ChangeRequestDetailSerializer(self.get_queryset().get(pk=pk)).data, code)

    @extend_schema(parameters=[STATUS_PARAM])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=ChangeRequestCreateSerializer, responses={201: ChangeRequestDetailSerializer}
    )
    def create(self, request, *args, **kwargs):
        serializer = ChangeRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = Tenant.objects.get(pk=request.tenant_id)
        change_request = services.raise_request(tenant, request.user, serializer.validated_data)
        return self._detail(change_request.pk, status.HTTP_201_CREATED)

    @extend_schema(request=CommentCreateSerializer, responses={201: ChangeRequestDetailSerializer})
    @action(detail=True, methods=["post"])
    def comments(self, request, pk=None):
        serializer = CommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_request = self.get_object()
        services.add_comment(
            change_request, request.user, serializer.validated_data["body"], from_staff=False
        )
        return self._detail(change_request.pk, status.HTTP_201_CREATED)


@extend_schema(tags=["admin: change requests"])
class AdminChangeRequestViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """The change requests queue (admin spec 2.9, Workflow 3.6). Both platform roles."""

    permission_classes = [IsPlatformStaff]
    pagination_class = ChangeRequestPagination
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "list":
            return AdminChangeRequestSerializer
        return AdminChangeRequestDetailSerializer

    def get_queryset(self):
        queryset = _base_queryset()
        params = self.request.query_params
        if (value := params.get("status")) in ChangeRequest.Status.values:
            queryset = queryset.filter(status=value)
        elif params.get("status") == "active":
            queryset = queryset.filter(status__in=["open", "in_progress"])
        if tenant := params.get("tenant"):
            queryset = queryset.filter(tenant_id=tenant)
        if (value := params.get("request_type")) in ChangeRequest.RequestType.values:
            queryset = queryset.filter(request_type=value)
        assignee = params.get("assignee")
        if assignee == "me":
            queryset = queryset.filter(assignee=self.request.user)
        elif assignee == "none":
            queryset = queryset.filter(assignee=None)
        elif assignee:
            queryset = queryset.filter(assignee_id=assignee)
        if search := params.get("search", "").strip():
            queryset = queryset.filter(
                Q(subject__icontains=search) | Q(tenant__name__icontains=search)
            )
        return queryset.order_by("-created_at")

    def _detail(self, pk, code=status.HTTP_200_OK):
        return Response(AdminChangeRequestDetailSerializer(_base_queryset().get(pk=pk)).data, code)

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, enum=[*ChangeRequest.Status.values, "active"]),
            OpenApiParameter("tenant", str),
            OpenApiParameter("assignee", str, description="A staff user id, `me` or `none`"),
            OpenApiParameter("request_type", str, enum=ChangeRequest.RequestType.values),
            OpenApiParameter("search", str),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=AdminChangeRequestUpdateSerializer, responses=AdminChangeRequestDetailSerializer
    )
    def partial_update(self, request, pk=None):
        serializer = AdminChangeRequestUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        change_request = self.get_object()
        if "assignee" in data:
            change_request = services.assign(change_request, request.user, data["assignee"])
        if data.get("status") == "in_progress":
            change_request = services.start_work(change_request, request.user)
        return self._detail(change_request.pk)

    @extend_schema(
        request=CommentCreateSerializer, responses={201: AdminChangeRequestDetailSerializer}
    )
    @action(detail=True, methods=["post"])
    def comments(self, request, pk=None):
        serializer = CommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_request = self.get_object()
        services.add_comment(
            change_request, request.user, serializer.validated_data["body"], from_staff=True
        )
        return self._detail(change_request.pk, status.HTTP_201_CREATED)

    @extend_schema(request=DeclineSerializer, responses=AdminChangeRequestDetailSerializer)
    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        serializer = DeclineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_request = services.decline(
            self.get_object(), request.user, serializer.validated_data["reason"]
        )
        return self._detail(change_request.pk)

    @extend_schema(request=LinkVersionSerializer, responses=AdminChangeRequestDetailSerializer)
    @action(detail=True, methods=["post"], url_path="link-version")
    def link_version(self, request, pk=None):
        serializer = LinkVersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_request = self.get_object()
        version = get_object_or_404(
            WhatsAppTemplateVersion.objects.select_related("template"),
            pk=serializer.validated_data["version"],
        )
        change_request = services.link_version(change_request, request.user, version)
        return self._detail(change_request.pk)

    @extend_schema(responses=CandidateVersionSerializer(many=True))
    @action(detail=True, methods=["get"], pagination_class=None)
    def versions(self, request, pk=None):
        """Template versions of this client that are still in progress (can be linked)."""
        change_request = self.get_object()
        versions = candidate_versions(change_request.tenant_id)
        return Response(CandidateVersionSerializer(versions, many=True).data)

    @extend_schema(responses=StaffUserSerializer(many=True))
    @action(detail=False, methods=["get"], pagination_class=None)
    def assignees(self, request):
        """Platform staff a request can be assigned to."""
        staff = User.objects.filter(
            is_active=True, platform_role__in=["super_admin", "support"]
        ).order_by("full_name", "email")
        return Response(StaffUserSerializer(staff, many=True).data)
