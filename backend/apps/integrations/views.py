"""Integrations screen API (portal spec 2.10): owner only; platform support may not see it."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import tenant_roles
from apps.core.models import Tenant
from apps.core.portal import PortalViewMixin

from . import keys, webhooks
from .models import EVENT_TYPES, ApiKey, WebhookDelivery, WebhookEndpoint
from .serializers import (
    ApiKeyCreatedSerializer,
    ApiKeyCreateSerializer,
    ApiKeySerializer,
    DeliverySerializer,
    EndpointCreateSerializer,
    EndpointSerializer,
    EndpointUpdateSerializer,
    EventTypeSerializer,
)

TAGS = ["portal: integrations"]


class OwnerNotSupport(tenant_roles("owner")):
    """Owner only; unlike other screens, support sessions can't even view it (spec 1.1)."""

    def has_permission(self, request, view):
        if getattr(request, "support_session_id", None):
            return False
        return super().has_permission(request, view)


class IntegrationsMixin(PortalViewMixin):
    permission_classes = [OwnerNotSupport]

    def tenant(self):
        return Tenant.objects.get(pk=self.request.tenant_id)


@extend_schema(tags=TAGS)
class ApiKeyViewSet(IntegrationsMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ApiKeySerializer
    pagination_class = None

    def get_queryset(self):
        return self.scoped(ApiKey.objects.select_related("created_by"))

    @extend_schema(request=ApiKeyCreateSerializer, responses={201: ApiKeyCreatedSerializer})
    def create(self, request):
        data = ApiKeyCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        key, raw = keys.create_key(self.tenant(), data.validated_data["name"], request.user)
        return Response({"key": ApiKeySerializer(key).data, "secret": raw}, status=201)

    @extend_schema(request=None, responses=ApiKeySerializer)
    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        return Response(ApiKeySerializer(keys.revoke_key(self.get_object(), request.user)).data)


@extend_schema(tags=TAGS)
class WebhookEndpointViewSet(
    IntegrationsMixin, mixins.ListModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet
):
    serializer_class = EndpointSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return self.scoped(WebhookEndpoint.objects.all())

    @extend_schema(request=EndpointCreateSerializer, responses={201: EndpointSerializer})
    def create(self, request):
        data = EndpointCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        endpoint = webhooks.create_endpoint(
            self.tenant(), data.validated_data["url"], data.validated_data["events"], request.user
        )
        return Response(EndpointSerializer(endpoint).data, status=201)

    @extend_schema(request=EndpointUpdateSerializer, responses=EndpointSerializer)
    def partial_update(self, request, pk=None):
        data = EndpointUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        endpoint = webhooks.update_endpoint(self.get_object(), data.validated_data, request.user)
        return Response(EndpointSerializer(endpoint).data)

    @extend_schema(request=None, responses=EndpointSerializer)
    @action(detail=True, methods=["post"], url_path="rotate-secret")
    def rotate_secret(self, request, pk=None):
        endpoint = webhooks.update_endpoint(self.get_object(), {}, request.user, rotate_secret=True)
        return Response(EndpointSerializer(endpoint).data)

    @extend_schema(request=None, responses={202: DeliverySerializer})
    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        """Send a `test` event to this endpoint now."""
        endpoint = self.get_object()
        [delivery] = webhooks.emit(
            endpoint.tenant_id,
            "test",
            {"message": "Test event from Schedule Confirmation", "endpoint_id": str(endpoint.pk)},
            endpoints=[endpoint],
        )
        delivery.endpoint = endpoint
        return Response(DeliverySerializer(delivery).data, status=status.HTTP_202_ACCEPTED)


class DeliveryPagination(PageNumberPagination):
    page_size = 50


@extend_schema(tags=TAGS)
class DeliveryViewSet(
    IntegrationsMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """The delivery log, newest first."""

    serializer_class = DeliverySerializer
    pagination_class = DeliveryPagination

    def get_queryset(self):
        queryset = self.scoped(WebhookDelivery.objects.select_related("endpoint"))
        params = self.request.query_params
        if endpoint := params.get("endpoint"):
            queryset = queryset.filter(endpoint_id=endpoint)
        if (value := params.get("status")) in WebhookDelivery.Status.values:
            queryset = queryset.filter(status=value)
        if event := params.get("event_id"):
            queryset = queryset.filter(event_id=event)
        return queryset.order_by("-created_at")

    @extend_schema(
        parameters=[
            OpenApiParameter("endpoint", str),
            OpenApiParameter("status", str, enum=WebhookDelivery.Status.values),
            OpenApiParameter("event_id", str),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=None, responses=DeliverySerializer)
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        delivery = webhooks.retry_now(self.get_object(), request.user)
        return Response(DeliverySerializer(delivery).data)


class EventTypesView(APIView):
    permission_classes = [OwnerNotSupport]

    @extend_schema(tags=TAGS, responses=EventTypeSerializer(many=True))
    def get(self, request):
        return Response([{"code": code, "label": str(label)} for code, label in EVENT_TYPES])
