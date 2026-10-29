import re

from django.db.models import Prefetch, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.accounts.permissions import tenant_roles
from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.portal import PortalViewMixin
from apps.jobs.models import FollowUpItem, Job

from . import services
from .models import Schedule
from .serializers import (
    BookingSerializer,
    BulkSetDateResultSerializer,
    BulkSetDateSerializer,
    CancelReasonSerializer,
    SetDateSerializer,
    TimelineEntrySerializer,
)

ALL_ROLES = tenant_roles("owner", "manager", "office_staff")


class BookingPagination(PageNumberPagination):
    page_size = 50


BOOKING_FILTERS = [
    OpenApiParameter("date", str, description="Delivery date (scheduled_date), YYYY-MM-DD"),
    OpenApiParameter("date_from", str),
    OpenApiParameter("date_to", str),
    OpenApiParameter("no_date", bool, description="Only bookings with no delivery date yet"),
    OpenApiParameter("status", str),
    OpenApiParameter("outcome", str, description="An outcome, or 'none'"),
    OpenApiParameter("area", str, description="Area id, or 'none'"),
    OpenApiParameter("search", str, description="Booking reference, customer name or mobile"),
]


@extend_schema(tags=["portal: bookings"])
class BookingViewSet(
    PortalViewMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Bookings (spec 2.3). Date changes, cancel and outcomes go through apps.schedules.services."""

    serializer_class = BookingSerializer
    permission_classes = [ALL_ROLES]
    pagination_class = BookingPagination
    http_method_names = ["get", "post", "patch", "head", "options"]

    @property
    def actor(self):
        """The signed-in user, or None for the public API (API keys act for the agency)."""
        user = self.request.user
        return user if user.is_authenticated else None

    def get_queryset(self):
        queryset = self.scoped(
            Schedule.objects.select_related("subscriber__area", "schedule_type").prefetch_related(
                Prefetch("jobs", queryset=Job.objects.select_related("assigned_staff")),
                Prefetch("follow_ups", queryset=FollowUpItem.objects.all()),
            )
        )
        params = self.request.query_params
        if date := params.get("date"):
            queryset = queryset.filter(scheduled_date=date)
        if date_from := params.get("date_from"):
            queryset = queryset.filter(scheduled_date__gte=date_from)
        if date_to := params.get("date_to"):
            queryset = queryset.filter(scheduled_date__lte=date_to)
        if params.get("no_date") == "true":
            queryset = queryset.filter(scheduled_date=None)
        if status := params.get("status"):
            queryset = queryset.filter(status=status)
        if outcome := params.get("outcome"):
            queryset = queryset.filter(outcome="" if outcome == "none" else outcome)
        if area := params.get("area"):
            queryset = (
                queryset.filter(subscriber__area=None)
                if area == "none"
                else queryset.filter(subscriber__area_id=area)
            )
        if search := params.get("search", "").strip():
            condition = Q(booking_reference__icontains=search) | Q(
                subscriber__customer_name__icontains=search
            )
            digits = re.sub(r"\D", "", search)
            if len(digits) >= 4:
                condition |= Q(subscriber__mobile_number__contains=digits[-10:])
            queryset = queryset.filter(condition)
        if params.get("date") or params.get("date_from"):
            return queryset.order_by("scheduled_date", "booking_reference")
        return queryset

    @extend_schema(parameters=BOOKING_FILTERS)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.instance = services.create_booking(
            serializer.context["tenant"], serializer.validated_data, self.actor
        )

    def perform_update(self, serializer):
        new_date = serializer.validated_data.pop(
            "scheduled_date", serializer.instance.scheduled_date
        )
        before = snapshot(serializer.instance)
        if serializer.validated_data:
            serializer.save()
            after = snapshot(serializer.instance)
            if after != before:
                write_audit(Action.UPDATE, serializer.instance, before=before, after=after)
                services.event(
                    serializer.instance,
                    "updated",
                    self.actor,
                    fields=sorted(k for k in after if after[k] != before.get(k)),
                )
        if new_date != serializer.instance.scheduled_date:
            if new_date is None:
                from apps.core.exceptions import ApiError

                raise ApiError("invalid_date", "A delivery date can be changed, not removed.", 400)
            serializer.instance = services.set_date(serializer.instance, new_date, self.actor)

    def _fresh(self, schedule):
        return self.get_serializer(self.get_queryset().get(pk=schedule.pk)).data

    @extend_schema(request=SetDateSerializer, responses=BookingSerializer)
    @action(detail=True, methods=["post"], url_path="set-date")
    def set_date(self, request, **kwargs):
        data = SetDateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        schedule = services.set_date(
            self.get_object(), data.validated_data["scheduled_date"], self.actor
        )
        return Response(self._fresh(schedule))

    @extend_schema(request=BulkSetDateSerializer, responses=BulkSetDateResultSerializer)
    @action(detail=False, methods=["post"], url_path="bulk-set-date")
    def bulk_set_date(self, request):
        data = BulkSetDateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        result = services.bulk_set_date(
            request.tenant_id,
            data.validated_data["ids"],
            data.validated_data["scheduled_date"],
            self.actor,
        )
        return Response(result)

    @extend_schema(request=CancelReasonSerializer, responses=BookingSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, **kwargs):
        data = CancelReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        schedule = services.cancel_booking(
            self.get_object(), data.validated_data["reason"], self.actor
        )
        return Response(self._fresh(schedule))

    @extend_schema(responses=TimelineEntrySerializer(many=True))
    @action(detail=True, methods=["get"], pagination_class=None)
    def timeline(self, request, **kwargs):
        """Every status change, reminder attempt, reply and note, oldest first (spec 2.3)."""
        schedule = self.get_object()
        entries = [
            {
                "at": e.occurred_at,
                "kind": e.kind,
                "data": e.data,
                "actor_email": e.actor.email if e.actor else "",
            }
            for e in schedule.events.select_related("actor")
        ]
        for attempt in schedule.attempts.prefetch_related("responses"):
            entries.append(
                {
                    "at": attempt.sent_at or attempt.created_at,
                    "kind": "attempt",
                    "data": {
                        "channel": attempt.channel,
                        "delivery_status": attempt.delivery_status,
                        "failure_reason": attempt.failure_reason,
                        "cost_paise": attempt.cost_paise,
                    },
                    "actor_email": "",
                }
            )
            for reply in attempt.responses.all():
                entries.append(
                    {
                        "at": reply.received_at,
                        "kind": "response",
                        "data": {
                            "channel": attempt.channel,
                            "text": reply.raw_text,
                            "button": reply.button_payload,
                            "transcript": reply.transcript,
                            "outcome": reply.outcome,
                            "confidence": float(reply.confidence)
                            if reply.confidence is not None
                            else None,
                            "has_recording": bool(reply.recording_key),
                        },
                        "actor_email": "",
                    }
                )
        # At the same moment, a message comes before the reply and the reply before what it caused.
        rank = {"attempt": 0, "response": 1}
        entries.sort(key=lambda entry: (entry["at"], rank.get(entry["kind"], 2)))
        return Response(TimelineEntrySerializer(entries, many=True).data)
