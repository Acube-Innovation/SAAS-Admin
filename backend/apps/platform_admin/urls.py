from rest_framework.routers import SimpleRouter

from apps.industry.views import IndustryTemplateViewSet
from apps.messaging_templates.views import WhatsAppTemplateViewSet

from .views import SupportSessionViewSet, TenantViewSet

router = SimpleRouter(trailing_slash=False)
router.register("tenants", TenantViewSet, basename="admin-tenant")
router.register("industry-templates", IndustryTemplateViewSet, basename="admin-industry-template")
router.register("whatsapp-templates", WhatsAppTemplateViewSet, basename="admin-whatsapp-template")
router.register("support-sessions", SupportSessionViewSet, basename="admin-support-session")

urlpatterns = router.urls
