"""Audit log screen API (spec 2.8): filtered list, filter facets and audited CSV export."""

import csv
import datetime as dt
import io
import json
import zoneinfo

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsPlatformStaff
from apps.core.exceptions import ApiError
from apps.core.models import Tenant

from .models import Action, AuditLog
from .services import write_audit

IST = zoneinfo.ZoneInfo("Asia/Kolkata")
EXPORT_LIMIT = 50_000
FILTER_PARAMS = [
    OpenApiParameter("tenant", str, description="Client id"),
    OpenApiParameter("actor", str, description="Part of the actor's email"),
    OpenApiParameter("action", str),
    OpenApiParameter("object_type", str),
    OpenApiParameter("date_from", dt.date, description="From this day (India time), inclusive"),
    OpenApiParameter("date_to", dt.date, description="Up to this day (India time), inclusive"),
]


class AuditEntrySerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.name", allow_null=True, default=None)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "created_at",
            "tenant_id",
            "tenant_name",
            "actor_id",
            "actor_email",
            "support_session_id",
            "action",
            "object_type",
            "object_id",
            "before",
            "after",
            "ip_address",
            "user_agent",
        ]


def _date(value, name):
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ApiError("validation_error", f"{name}: use YYYY-MM-DD.", 400) from None


def filtered_entries(params):
    """Audit entries matching the screen's filters (platform staff see every tenant)."""
    queryset = AuditLog.objects.select_related("tenant").order_by("-created_at", "-id")
    if tenant := params.get("tenant"):
        queryset = queryset.filter(tenant_id=tenant)
    if actor := params.get("actor", "").strip():
        queryset = queryset.filter(actor_email__icontains=actor)
    if action := params.get("action"):
        queryset = queryset.filter(action=action)
    if object_type := params.get("object_type"):
        queryset = queryset.filter(object_type=object_type)
    if date_from := params.get("date_from"):
        start = dt.datetime.combine(_date(date_from, "date_from"), dt.time.min, tzinfo=IST)
        queryset = queryset.filter(created_at__gte=start)
    if date_to := params.get("date_to"):
        end = dt.datetime.combine(
            _date(date_to, "date_to") + dt.timedelta(days=1), dt.time.min, tzinfo=IST
        )
        queryset = queryset.filter(created_at__lt=end)
    return queryset


class AuditLogPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class AuditLogListView(ListAPIView):
    permission_classes = [IsPlatformStaff]
    serializer_class = AuditEntrySerializer
    pagination_class = AuditLogPagination

    def get_queryset(self):
        return filtered_entries(self.request.query_params)

    @extend_schema(parameters=FILTER_PARAMS, tags=["admin: audit log"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AuditLogFacetsView(APIView):
    """Values for the Client, Action and Record filters."""

    permission_classes = [IsPlatformStaff]

    @extend_schema(
        tags=["admin: audit log"],
        responses=inline_serializer(
            "AuditLogFacets",
            {
                "actions": serializers.ListField(child=serializers.CharField()),
                "object_types": serializers.ListField(child=serializers.CharField()),
                "tenants": inline_serializer(
                    "AuditLogTenant",
                    {"id": serializers.UUIDField(), "name": serializers.CharField()},
                    many=True,
                ),
            },
        ),
    )
    def get(self, request):
        def distinct(field):
            return sorted(
                v for v in AuditLog.objects.order_by().values_list(field, flat=True).distinct() if v
            )

        tenants = Tenant.objects.filter(
            pk__in=AuditLog.objects.order_by().values("tenant_id").distinct()
        ).order_by("name")
        return Response(
            {
                "actions": distinct("action"),
                "object_types": distinct("object_type"),
                "tenants": [{"id": t.pk, "name": t.name} for t in tenants],
            }
        )


CSV_COLUMNS = [
    "created_at",
    "actor_email",
    "tenant",
    "action",
    "object_type",
    "object_id",
    "support_session_id",
    "ip_address",
    "before",
    "after",
]


def safe_cell(value) -> str:
    """Neutralise spreadsheet formulas (CSV injection): cells starting with = + - @ get a '."""
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class AuditLogExportView(APIView):
    """CSV of every entry matching the filters, newest first. The export is itself audited."""

    permission_classes = [IsPlatformStaff]

    @extend_schema(
        parameters=FILTER_PARAMS,
        tags=["admin: audit log"],
        responses={(200, "text/csv"): str},
    )
    def get(self, request):
        queryset = filtered_entries(request.query_params)
        total = queryset.count()
        if total > EXPORT_LIMIT:
            raise ApiError(
                "export_too_large",
                f"{total} entries match. Narrow the filters to {EXPORT_LIMIT} or fewer.",
                400,
            )
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_COLUMNS)
        # Rows are read now, inside the request's tenant/platform context (RLS).
        for entry in queryset.iterator(chunk_size=2000):
            writer.writerow(
                [
                    safe_cell(
                        timezone.localtime(entry.created_at, IST).isoformat(timespec="seconds")
                    ),
                    safe_cell(entry.actor_email),
                    safe_cell(entry.tenant.name if entry.tenant else ""),
                    safe_cell(entry.action),
                    safe_cell(entry.object_type),
                    safe_cell(entry.object_id),
                    safe_cell(entry.support_session_id),
                    safe_cell(entry.ip_address),
                    safe_cell(
                        json.dumps(entry.before, ensure_ascii=False)
                        if entry.before is not None
                        else ""
                    ),
                    safe_cell(
                        json.dumps(entry.after, ensure_ascii=False)
                        if entry.after is not None
                        else ""
                    ),
                ]
            )
        filters = {
            k: v for k, v in request.query_params.items() if k in {p.name for p in FILTER_PARAMS}
        }
        write_audit(
            Action.EXPORT,
            object_type="audit.auditlog",
            after={"filters": filters, "rows": total},
            tenant=None,
        )
        stamp = timezone.localtime(timezone.now(), IST).strftime("%Y%m%d-%H%M")
        response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="audit-log-{stamp}.csv"'
        return response
