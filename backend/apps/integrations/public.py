"""
Public API for clients' own systems (portal spec 7): /api/v1/, authenticated with an API key
(`Authorization: Bearer <key>`, see middleware.py) and rate-limited per key. Subscribers are found
by `external_ref` (consumer number) and bookings by `booking_reference`. Writes are audited and
send the same webhooks as the portal.
"""

from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission

from apps.core.portal import PortalViewMixin
from apps.schedules.models import ScheduleEvent
from apps.schedules.views import BookingViewSet
from apps.subscribers.views import SubscriberViewSet

from .serializers import OutcomeEventSerializer

TAGS = ["public api v1"]


class HasApiKey(BasePermission):
    message = "Send a valid API key."

    def has_permission(self, request, view):
        return getattr(request, "api_key", None) is not None and bool(request.tenant_id)


class PublicMixin:
    authentication_classes = []
    permission_classes = [HasApiKey]


@extend_schema(tags=TAGS)
class PublicSubscriberViewSet(PublicMixin, SubscriberViewSet):
    lookup_field = "external_ref"
    lookup_value_regex = "[^/]+"


@extend_schema(tags=TAGS)
class PublicScheduleViewSet(PublicMixin, BookingViewSet):
    lookup_field = "booking_reference"
    lookup_value_regex = "[^/]+"


class OutcomePagination(PageNumberPagination):
    page_size = 100


@extend_schema(tags=TAGS)
class PublicOutcomeViewSet(
    PublicMixin, PortalViewMixin, mixins.ListModelMixin, viewsets.GenericViewSet
):
    """Answers recorded since a time, oldest first: poll with `since` (or use webhooks)."""

    serializer_class = OutcomeEventSerializer
    pagination_class = OutcomePagination

    def get_queryset(self):
        queryset = self.scoped(
            ScheduleEvent.objects.filter(kind="outcome_set").select_related("schedule__subscriber")
        )
        if since := self.request.query_params.get("since"):
            moment = parse_datetime(since)
            if moment is None:
                raise ValidationError({"since": ["Use an ISO 8601 date and time."]})
            queryset = queryset.filter(occurred_at__gt=moment)
        return queryset.order_by("occurred_at")

    @extend_schema(parameters=[OpenApiParameter("since", str, description="ISO 8601 date-time")])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
