"""
Communication log (portal spec 2.8) and Reports (2.5). Read-only; recording plays and CSV exports
are audited.
"""

import csv
import datetime as dt
import io

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import tenant_roles
from apps.audit.api import safe_cell
from apps.audit.models import Action
from apps.audit.services import write_audit
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.portal import PortalViewMixin
from apps.core.storage import signed_url
from apps.schedules.models import Attempt
from apps.schedules.recordings import LINK_SECONDS, is_expired

from . import services
from .serializers import (
    FollowUpAgeingSerializer,
    LogEntrySerializer,
    RecordingLinkSerializer,
    ReminderResultsSerializer,
    SubscriberHealthSerializer,
)

ALL_ROLES = tenant_roles("owner", "manager", "office_staff")
# Reports and recordings: owner and manager (platform support may view in a support session).
MANAGERS = tenant_roles("owner", "manager")
EXPORT_ROW_LIMIT = 50_000

DATE_PARAMS = [
    OpenApiParameter("date_from", dt.date, description="From this day (India time), inclusive"),
    OpenApiParameter("date_to", dt.date, description="To this day (India time), inclusive"),
]
AREA_PARAM = OpenApiParameter("area", str, description="An area id, or `none`")
LOG_PARAMS = [
    *DATE_PARAMS,
    OpenApiParameter("channel", str, enum=Attempt.Channel.values),
    OpenApiParameter("delivery_status", str, enum=Attempt.DeliveryStatus.values),
    OpenApiParameter("outcome", str),
    OpenApiParameter("booking", str),
    OpenApiParameter("subscriber", str),
    OpenApiParameter("has_recording", bool),
    OpenApiParameter("search", str, description="Booking reference, name, mobile or consumer no."),
]


class LogPagination(PageNumberPagination):
    page_size = 50


@extend_schema(tags=["portal: communication log and reports"])
class CommunicationLogViewSet(
    PortalViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Every message and call with its replies; call transcripts; 5-minute recording links."""

    serializer_class = LogEntrySerializer
    pagination_class = LogPagination

    def get_permissions(self):
        return [MANAGERS()] if self.action == "recording" else [ALL_ROLES()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Attempt.objects.none()
        return services.communication_log(self.request.tenant_id, self.request.query_params)

    @extend_schema(parameters=LOG_PARAMS)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(responses=RecordingLinkSerializer)
    @action(detail=True, methods=["get"])
    def recording(self, request, pk=None):
        """A presigned link to the call recording, valid for 5 minutes. Every play is audited."""
        attempt = self.scoped(Attempt.objects.all()).filter(pk=pk).first()
        if attempt is None:
            raise ApiError("not_found", "Not found.", 404)
        reply = attempt.responses.exclude(recording_key="").order_by("-received_at").first()
        if reply is None:
            raise ApiError("no_recording", "This call has no recording.", 404)
        if is_expired(reply.received_at):
            raise ApiError("recording_expired", "Recordings are kept for 90 days.", 410)
        url = signed_url(reply.recording_key, expires=LINK_SECONDS)
        write_audit(
            Action.RECORDING_PLAYED,
            reply,
            after={
                "attempt": str(attempt.pk),
                "recording_key": reply.recording_key,
                "expires_in": LINK_SECONDS,
            },
        )
        return Response(
            RecordingLinkSerializer(
                {
                    "url": url,
                    "expires_in": LINK_SECONDS,
                    "expires_at": timezone.now() + dt.timedelta(seconds=LINK_SECONDS),
                }
            ).data
        )


class _ReportView(APIView):
    permission_classes = [MANAGERS]

    def tenant(self):
        return Tenant.objects.get(pk=self.request.tenant_id)


class ReminderResultsView(_ReportView):
    """Reminder results: sent, delivered, replied, and each outcome as count and share."""

    @extend_schema(
        tags=["portal: communication log and reports"],
        parameters=[*DATE_PARAMS, AREA_PARAM, OpenApiParameter("cylinder_type", str)],
        responses=ReminderResultsSerializer,
    )
    def get(self, request):
        params = request.query_params
        date_from, date_to = services.date_range(params)
        data = services.reminder_results(
            self.tenant(), date_from, date_to, params.get("area"), params.get("cylinder_type")
        )
        return Response(ReminderResultsSerializer(data).data)


class FollowUpAgeingView(_ReportView):
    """Follow-up ageing: open items by waiting time, per outcome."""

    @extend_schema(
        tags=["portal: communication log and reports"],
        parameters=[AREA_PARAM, OpenApiParameter("outcome", str)],
        responses=FollowUpAgeingSerializer,
    )
    def get(self, request):
        params = request.query_params
        data = services.follow_up_ageing(self.tenant(), params.get("area"), params.get("outcome"))
        return Response(FollowUpAgeingSerializer(data).data)


class SubscriberHealthView(_ReportView):
    """Subscriber health: no consent, opted out, failed numbers; by area."""

    @extend_schema(
        tags=["portal: communication log and reports"],
        parameters=[AREA_PARAM],
        responses=SubscriberHealthSerializer,
    )
    def get(self, request):
        data = services.subscriber_health(self.tenant(), request.query_params.get("area"))
        return Response(SubscriberHealthSerializer(data).data)


# --- CSV exports ------------------------------------------------------------------------------


def _ist(value):
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if value else ""


def _yes(flag):
    return "yes" if flag else "no"


def _reminder_results_csv(tenant, params):
    date_from, date_to = services.date_range(params)
    data = services.reminder_results(
        tenant, date_from, date_to, params.get("area"), params.get("cylinder_type")
    )
    rows = [
        ["sent", data["sent"], ""],
        ["delivered", data["delivered"], ""],
        ["failed", data["failed"], ""],
        ["replied", data["replied"], ""],
        ["calls", data["calls"], ""],
        ["calls_answered", data["calls_answered"], ""],
        ["reminded_bookings", data["reminded"], ""],
    ]
    rows += [[f"outcome:{o['outcome']}", o["count"], o["share"]] for o in data["outcomes"]]
    name = f"reminder-results-{date_from}-to-{date_to}"
    return ["metric", "count", "share_percent"], rows, name


def _follow_up_ageing_csv(tenant, params):
    data = services.follow_up_ageing(tenant, params.get("area"), params.get("outcome"))
    keys = [key for key, _low, _high in services.AGEING_BUCKETS]
    rows = [[r["outcome"], *[r[k] for k in keys], r["total"]] for r in data["by_outcome"]]
    rows.append(["total", *[b["count"] for b in data["buckets"]], data["total"]])
    name = f"follow-up-ageing-{timezone.localdate()}"
    return ["outcome", *keys, "total"], rows, name


def _subscriber_health_csv(tenant, params):
    rows = [
        [
            s.external_ref,
            s.customer_name,
            s.mobile_number,
            s.area.name if s.area else "",
            _yes(not s.whatsapp_consent),
            _yes(s.opted_out),
            _yes(s.last_status == Attempt.DeliveryStatus.FAILED),
            (s.last_failure or "") if s.last_status == Attempt.DeliveryStatus.FAILED else "",
        ]
        for s in services.subscriber_health_rows(tenant, params.get("area"))[:EXPORT_ROW_LIMIT]
    ]
    columns = [
        "external_ref",
        "customer_name",
        "mobile_number",
        "area",
        "no_consent",
        "opted_out",
        "last_message_failed",
        "failure_reason",
    ]
    return columns, rows, f"subscriber-health-{timezone.localdate()}"


def _message_log_csv(tenant, params):
    rows = []
    for attempt in services.communication_log(tenant, params)[:EXPORT_ROW_LIMIT]:
        subscriber = attempt.schedule.subscriber
        replies = list(attempt.responses.all()) or [None]
        for reply in replies:
            rows.append(
                [
                    _ist(attempt.sent_at),
                    attempt.channel,
                    attempt.schedule.booking_reference,
                    subscriber.external_ref,
                    subscriber.customer_name,
                    subscriber.mobile_number,
                    subscriber.area.name if subscriber.area else "",
                    attempt.delivery_status,
                    attempt.failure_reason,
                    f"{attempt.cost_paise / 100:.2f}",
                    _ist(reply.received_at) if reply else "",
                    (reply.raw_text or reply.transcript) if reply else "",
                    reply.outcome if reply else "",
                ]
            )
    columns = [
        "sent_at",
        "channel",
        "booking_reference",
        "external_ref",
        "customer_name",
        "mobile_number",
        "area",
        "delivery_status",
        "failure_reason",
        "cost_rupees",
        "reply_at",
        "reply",
        "outcome",
    ]
    return columns, rows, f"message-log-{timezone.localdate()}"


EXPORTS = {
    "reminder-results": _reminder_results_csv,
    "follow-up-ageing": _follow_up_ageing_csv,
    "subscriber-health": _subscriber_health_csv,
    "message-log": _message_log_csv,
}


class ReportExportView(_ReportView):
    """Any report as CSV, with the same filters as the screen. Every export is audited."""

    @extend_schema(
        tags=["portal: communication log and reports"],
        parameters=[
            *LOG_PARAMS,
            AREA_PARAM,
            OpenApiParameter("cylinder_type", str),
        ],
        responses={(200, "text/csv"): str},
    )
    def get(self, request, report):
        build = EXPORTS.get(report)
        if build is None:
            raise ApiError("not_found", "Unknown report.", 404)
        tenant = self.tenant()
        columns, rows, filename = build(tenant, request.query_params)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([safe_cell(v) for v in row])
        write_audit(
            Action.EXPORT,
            object_type=f"reports.{report}",
            after={"filters": request.query_params.dict(), "rows": len(rows)},
            tenant=tenant,
        )
        response = HttpResponse("﻿" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
        return response
