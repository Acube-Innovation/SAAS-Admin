from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.audit.api import AuditLogExportView, AuditLogFacetsView, AuditLogListView
from apps.billing.admin_views import (
    PlanAdminViewSet,
    PriceEntryAdminView,
    PriceListAdminView,
    TenantBillingView,
    TenantPlanChangeView,
    TenantPriceOverridesView,
    TenantPriceOverrideView,
)
from apps.change_requests.views import AdminChangeRequestViewSet
from apps.industry.views import IndustryTemplateViewSet
from apps.messaging_templates.dev import SimulateView
from apps.messaging_templates.views import WhatsAppTemplateViewSet

from .dashboard import DashboardView
from .views import ReferenceDataView, SupportSessionViewSet, TenantViewSet

router = SimpleRouter(trailing_slash=False)
router.register("tenants", TenantViewSet, basename="admin-tenant")
router.register("plans", PlanAdminViewSet, basename="admin-plan")
router.register("industry-templates", IndustryTemplateViewSet, basename="admin-industry-template")
router.register("whatsapp-templates", WhatsAppTemplateViewSet, basename="admin-whatsapp-template")
router.register("change-requests", AdminChangeRequestViewSet, basename="admin-change-request")
router.register("support-sessions", SupportSessionViewSet, basename="admin-support-session")

urlpatterns = [
    path("reference-data", ReferenceDataView.as_view(), name="admin-reference-data"),
    path("dashboard", DashboardView.as_view(), name="admin-dashboard"),
    path("price-list", PriceListAdminView.as_view(), name="admin-price-list"),
    path("price-list/<uuid:pk>", PriceEntryAdminView.as_view(), name="admin-price-entry"),
    path(
        "tenants/<uuid:pk>/subscription", TenantBillingView.as_view(), name="admin-tenant-billing"
    ),
    path(
        "tenants/<uuid:pk>/subscription/plan-change",
        TenantPlanChangeView.as_view(),
        name="admin-tenant-plan-change",
    ),
    path(
        "tenants/<uuid:pk>/price-overrides",
        TenantPriceOverridesView.as_view(),
        name="admin-tenant-price-overrides",
    ),
    path(
        "tenants/<uuid:pk>/price-overrides/<uuid:override_id>",
        TenantPriceOverrideView.as_view(),
        name="admin-tenant-price-override",
    ),
    path("audit-logs", AuditLogListView.as_view(), name="admin-audit-logs"),
    path("audit-logs/facets", AuditLogFacetsView.as_view(), name="admin-audit-log-facets"),
    path("audit-logs/export", AuditLogExportView.as_view(), name="admin-audit-log-export"),
    path(
        "dev/whatsapp-versions/<uuid:vid>/simulate",
        SimulateView.as_view(),
        name="admin-dev-whatsapp-simulate",
    ),
    *router.urls,
]
