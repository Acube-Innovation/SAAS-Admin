from rest_framework.routers import SimpleRouter

from .views import SupportSessionViewSet, TenantViewSet

router = SimpleRouter(trailing_slash=False)
router.register("tenants", TenantViewSet, basename="admin-tenant")
router.register("support-sessions", SupportSessionViewSet, basename="admin-support-session")

urlpatterns = router.urls
