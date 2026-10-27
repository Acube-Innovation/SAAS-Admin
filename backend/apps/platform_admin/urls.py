from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.industry.views import IndustryTemplateViewSet
from apps.messaging_templates.dev import SimulateView
from apps.messaging_templates.views import WhatsAppTemplateViewSet

from .views import ReferenceDataView, SupportSessionViewSet, TenantViewSet

router = SimpleRouter(trailing_slash=False)
router.register("tenants", TenantViewSet, basename="admin-tenant")
router.register("industry-templates", IndustryTemplateViewSet, basename="admin-industry-template")
router.register("whatsapp-templates", WhatsAppTemplateViewSet, basename="admin-whatsapp-template")
router.register("support-sessions", SupportSessionViewSet, basename="admin-support-session")

urlpatterns = [
    path("reference-data", ReferenceDataView.as_view(), name="admin-reference-data"),
    path(
        "dev/whatsapp-versions/<uuid:vid>/simulate",
        SimulateView.as_view(),
        name="admin-dev-whatsapp-simulate",
    ),
    *router.urls,
]
