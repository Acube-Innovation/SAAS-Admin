import datetime as dt

from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import tenant_roles
from apps.audit.mixins import AuditedModelMixin
from apps.audit.models import Action
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.portal import PortalViewMixin
from apps.schedules import services
from apps.schedules.models import Outcome, Schedule
from apps.schedules.serializers import CancelReasonSerializer, SetDateSerializer
from apps.schedules.services import next_delivery_day

from .models import FollowUpItem, Job, StaffMember
from .serializers import (
    DeliveriesSerializer,
    DeliveryJobSerializer,
    FollowUpSerializer,
    NoteSerializer,
    OptionalNoteSerializer,
    ReassignSerializer,
    StaffMemberSerializer,
)


@extend_schema(tags=["portal: areas and staff"])
class StaffMemberViewSet(AuditedModelMixin, PortalViewMixin, viewsets.ModelViewSet):
    """Delivery staff (portal spec 2.7). Owner and manager; no delete (deactivate instead)."""

    serializer_class = StaffMemberSerializer
    permission_classes = [tenant_roles("owner", "manager")]
    http_method_names = ["get", "post", "patch", "head", "options"]
    audit_status_fields = ("is_active",)

    @extend_schema(parameters=[OpenApiParameter("active", bool, description="Only active staff")])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        queryset = self.scoped(StaffMember.objects.all())
        if self.request.query_params.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        return queryset

    def perform_create(self, serializer):
        serializer.save(tenant_id=self.request.tenant_id)
        self.audit(
            Action.CREATE, serializer.instance, after=self.audit_snapshot(serializer.instance)
        )


# --- deliveries, jobs and the follow-up queue (spec 2.3, 2.4, Workflow 3.2) ------------------

ALL_ROLES = tenant_roles("owner", "manager", "office_staff")
MANAGERS = tenant_roles("owner", "manager")


@extend_schema(tags=["portal: deliveries and follow-up"])
class DeliveriesView(APIView):
    """Confirmed bookings for a date, grouped by area, with each job's staff member."""

    permission_classes = [ALL_ROLES]

    @extend_schema(
        parameters=[
            OpenApiParameter("date", str, description="YYYY-MM-DD; default: next delivery day")
        ],
        responses=DeliveriesSerializer,
    )
    def get(self, request):
        tenant = Tenant.objects.get(pk=request.tenant_id)
        raw = request.query_params.get("date")
        try:
            day = dt.date.fromisoformat(raw) if raw else next_delivery_day(tenant)
        except ValueError:
            raise ApiError("validation_error", "date: use YYYY-MM-DD.", 400) from None
        jobs = (
            Job.objects.filter(tenant=tenant, scheduled_date=day)
            .exclude(status=Job.Status.CANCELLED)
            .select_related("assigned_staff", "schedule__subscriber__area__default_staff")
            .order_by("schedule__subscriber__area__name", "schedule__subscriber__customer_name")
        )
        groups = {}
        for job in jobs:
            area = job.schedule.subscriber.area
            key = area.pk if area else None
            group = groups.setdefault(
                key,
                {
                    "area_id": key,
                    "area_name": area.name if area else None,
                    "default_staff_name": area.default_staff.name
                    if area and area.default_staff
                    else None,
                    "jobs": [],
                },
            )
            group["jobs"].append(job)
        others = (
            Schedule.objects.filter(tenant=tenant, scheduled_date=day)
            .exclude(outcome=Outcome.CONFIRMED)
            .exclude(status=Schedule.Status.CANCELLED)
            .values_list("outcome")
            .annotate(n=Count("id"))
        )
        data = {
            "date": day,
            "areas": sorted(
                groups.values(), key=lambda g: (g["area_name"] is None, g["area_name"] or "")
            ),
            "confirmed": len(jobs),
            "not_confirmed": {outcome or "awaiting": n for outcome, n in others},
        }
        return Response(DeliveriesSerializer(data).data)


@extend_schema(tags=["portal: deliveries and follow-up"])
class JobViewSet(PortalViewMixin, viewsets.GenericViewSet):
    queryset = Job.objects.none()
    permission_classes = [MANAGERS]

    def get_queryset(self):
        return self.scoped(Job.objects.select_related("schedule__subscriber", "assigned_staff"))

    @extend_schema(request=ReassignSerializer, responses=DeliveryJobSerializer)
    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        data = ReassignSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        staff = None
        if staff_id := data.validated_data["assigned_staff"]:
            staff = self.scoped(StaffMember.objects.all()).filter(pk=staff_id).first()
            if staff is None:
                raise ApiError("validation_error", "Unknown staff member.", 400)
        job = services.reassign_job(self.get_object(), staff, request.user)
        return Response(DeliveryJobSerializer(self.get_queryset().get(pk=job.pk)).data)


class FollowUpPagination(PageNumberPagination):
    page_size = 50


@extend_schema(tags=["portal: deliveries and follow-up"])
class FollowUpViewSet(
    PortalViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """The follow-up queue: every booking whose outcome is not Confirmed (spec 2.4)."""

    serializer_class = FollowUpSerializer
    permission_classes = [ALL_ROLES]
    pagination_class = FollowUpPagination

    def get_queryset(self):
        queryset = self.scoped(
            FollowUpItem.objects.select_related("schedule__subscriber__area").prefetch_related(
                "schedule__attempts__responses"
            )
        )
        params = self.request.query_params
        status = params.get("status")
        if status in FollowUpItem.Status.values:
            queryset = queryset.filter(status=status)
        elif self.action == "list":
            queryset = queryset.exclude(status=FollowUpItem.Status.RESOLVED)
        if outcome := params.get("outcome"):
            queryset = queryset.filter(outcome=outcome)
        if area := params.get("area"):
            queryset = (
                queryset.filter(schedule__subscriber__area=None)
                if area == "none"
                else queryset.filter(schedule__subscriber__area_id=area)
            )
        now = timezone.now()
        age = params.get("age")
        if age == "normal":
            queryset = queryset.filter(opened_at__gt=now - dt.timedelta(hours=4))
        elif age == "amber":
            queryset = queryset.filter(
                opened_at__lte=now - dt.timedelta(hours=4),
                opened_at__gt=now - dt.timedelta(hours=24),
            )
        elif age == "red":
            queryset = queryset.filter(opened_at__lte=now - dt.timedelta(hours=24))
        return queryset.order_by("opened_at")

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, enum=["open", "contacted", "resolved"]),
            OpenApiParameter("outcome", str),
            OpenApiParameter("area", str),
            OpenApiParameter("age", str, enum=["normal", "amber", "red"]),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def _respond(self, item):
        return Response(
            self.get_serializer(self.scoped(FollowUpItem.objects.all()).get(pk=item.pk)).data
        )

    @extend_schema(request=SetDateSerializer, responses=FollowUpSerializer)
    @action(detail=True, methods=["post"], url_path="set-date")
    def set_date(self, request, pk=None):
        data = SetDateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self._respond(
            services.follow_up_set_date(
                self.get_object(), data.validated_data["scheduled_date"], request.user
            )
        )

    @extend_schema(request=OptionalNoteSerializer, responses=FollowUpSerializer)
    @action(detail=True, methods=["post"])
    def contacted(self, request, pk=None):
        data = OptionalNoteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self._respond(
            services.follow_up_contacted(
                self.get_object(), request.user, data.validated_data["note"]
            )
        )

    @extend_schema(request=NoteSerializer, responses=FollowUpSerializer)
    @action(detail=True, methods=["post"])
    def note(self, request, pk=None):
        data = NoteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self._respond(
            services.follow_up_note(self.get_object(), data.validated_data["text"], request.user)
        )

    @extend_schema(request=CancelReasonSerializer, responses=FollowUpSerializer)
    @action(detail=True, methods=["post"], url_path="cancel-booking")
    def cancel_booking(self, request, pk=None):
        data = CancelReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self._respond(
            services.follow_up_cancel_booking(
                self.get_object(), data.validated_data["reason"], request.user
            )
        )

    @extend_schema(request=OptionalNoteSerializer, responses=FollowUpSerializer)
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        data = OptionalNoteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self._respond(
            services.follow_up_resolve(self.get_object(), request.user, data.validated_data["note"])
        )
