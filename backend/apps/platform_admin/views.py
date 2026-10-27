from django.db.models import Exists, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from django_otp.plugins.otp_totp.models import TOTPDevice
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import Membership, TenantRole
from apps.accounts.permissions import IsPlatformStaff, IsSuperAdmin
from apps.accounts.services import resend_invite
from apps.audit.models import AuditLog
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.tenancy import tenant_context

from . import services
from .models import SupportSession
from .serializers import (
    AuditLogSerializer,
    MembershipAdminSerializer,
    ReasonSerializer,
    SupportSessionSerializer,
    TenantCreateSerializer,
    TenantSerializer,
)
from .tenancy import SUPPORT_SESSION_KEY

ORDERINGS = {"name", "-name", "created_at", "-created_at"}


@extend_schema(tags=["admin: clients"])
class TenantViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Clients (tenants) for the super admin console: spec 2.3 to 2.5, Workflows 3.1 to 3.3."""

    serializer_class = TenantSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    super_admin_actions = {"create", "partial_update", "update", "suspend", "activate"}

    def get_permissions(self):
        if self.action in self.super_admin_actions:
            return [IsSuperAdmin()]
        return [IsPlatformStaff()]

    def get_serializer_class(self):
        return TenantCreateSerializer if self.action == "create" else TenantSerializer

    def get_queryset(self):
        owner_email = Membership.objects.filter(
            tenant=OuterRef("pk"), role=TenantRole.OWNER
        ).order_by("created_at")
        queryset = Tenant.objects.annotate(
            owner_email=Subquery(owner_email.values("user__email")[:1])
        )
        params = self.request.query_params
        if search := params.get("search", "").strip():
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(legal_name__icontains=search)
                | Q(gstin__icontains=search)
                | Q(owner_email__icontains=search)
            )
        if status_filter := params.get("status"):
            queryset = queryset.filter(status=status_filter)
        ordering = params.get("ordering", "name")
        return queryset.order_by(ordering if ordering in ORDERINGS else "name")

    @extend_schema(
        parameters=[
            OpenApiParameter("search", str, description="Name, legal name, GSTIN or owner email"),
            OpenApiParameter("status", str, enum=Tenant.Status.values),
            OpenApiParameter("ordering", str, enum=sorted(ORDERINGS)),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=TenantCreateSerializer, responses={201: TenantSerializer})
    def create(self, request, *args, **kwargs):
        serializer = TenantCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = services.create_client(serializer.validated_data, created_by=request.user)
        return Response(
            TenantSerializer(self.get_queryset().get(pk=tenant.pk)).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_update(self, serializer):
        services.update_tenant(serializer.instance, serializer.validated_data)

    def _reason(self, request):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["reason"]

    @extend_schema(request=ReasonSerializer, responses=TenantSerializer)
    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        tenant = self.get_object()
        services.suspend_tenant(tenant, self._reason(request))
        return Response(TenantSerializer(self.get_queryset().get(pk=pk)).data)

    @extend_schema(request=ReasonSerializer, responses=TenantSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        tenant = self.get_object()
        services.activate_tenant(tenant, self._reason(request))
        return Response(TenantSerializer(self.get_queryset().get(pk=pk)).data)

    @extend_schema(responses=MembershipAdminSerializer(many=True))
    @action(detail=True, methods=["get"])
    def users(self, request, pk=None):
        tenant = self.get_object()
        with tenant_context(tenant):
            memberships = list(
                Membership.objects.filter(tenant=tenant)
                .select_related("user")
                .annotate(
                    otp_enabled=Exists(
                        TOTPDevice.objects.filter(user=OuterRef("user"), confirmed=True)
                    )
                )
                .order_by("created_at")
            )
        return Response(MembershipAdminSerializer(memberships, many=True).data)

    @extend_schema(request=None, responses={204: None})
    @action(
        detail=True,
        methods=["post"],
        url_path=r"users/(?P<membership_id>[0-9a-f-]+)/resend-invite",
    )
    def resend_invite(self, request, pk=None, membership_id=None):
        tenant = self.get_object()
        with tenant_context(tenant):
            membership = get_object_or_404(Membership, pk=membership_id, tenant=tenant)
            resend_invite(membership, created_by=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses=AuditLogSerializer(many=True))
    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        tenant = self.get_object()
        entries = AuditLog.objects.filter(tenant=tenant).order_by("-created_at")
        page = self.paginate_queryset(entries)
        return self.get_paginated_response(AuditLogSerializer(page, many=True).data)

    @extend_schema(request=ReasonSerializer, responses={201: SupportSessionSerializer})
    @action(detail=True, methods=["post"], url_path="support-sessions")
    def support_sessions(self, request, pk=None):
        tenant = self.get_object()
        session = services.start_support_session(request.user, tenant, self._reason(request))
        request.session[SUPPORT_SESSION_KEY] = str(session.pk)
        return Response(SupportSessionSerializer(session).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["admin: support sessions"])
class SupportSessionViewSet(viewsets.GenericViewSet):
    """The signed-in staff user's support session: the banner's data and ending it."""

    permission_classes = [IsPlatformStaff]
    serializer_class = SupportSessionSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation has no user
            return SupportSession.objects.none()
        return SupportSession.objects.select_related("tenant", "staff_user").filter(
            staff_user=self.request.user
        )

    @extend_schema(responses={200: SupportSessionSerializer, 204: None})
    @action(detail=False, methods=["get"])
    def current(self, request):
        session = services.active_support_session(request.user)
        if session is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(SupportSessionSerializer(session).data)

    @extend_schema(request=None, responses=SupportSessionSerializer)
    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        session = get_object_or_404(self.get_queryset(), pk=pk)
        if session.ended_at is not None:
            raise ApiError("session_already_ended", "This support session has already ended.", 409)
        services.end_support_session(session, SupportSession.EndReason.MANUAL)
        if request.session.get(SUPPORT_SESSION_KEY) == str(session.pk):
            del request.session[SUPPORT_SESSION_KEY]
        return Response(SupportSessionSerializer(session).data)
