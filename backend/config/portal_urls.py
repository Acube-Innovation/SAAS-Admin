"""Client portal API (portal spec Section 7): /api/portal/, scoped to the request's tenant."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.jobs.views import StaffMemberViewSet
from apps.subscribers.views import (
    AreaViewSet,
    ImportJobViewSet,
    PortalConfigView,
    SubscriberViewSet,
)

router = SimpleRouter(trailing_slash=False)
router.register("subscribers", SubscriberViewSet, basename="portal-subscriber")
router.register("areas", AreaViewSet, basename="portal-area")
router.register("staff", StaffMemberViewSet, basename="portal-staff")
router.register("imports", ImportJobViewSet, basename="portal-import")

urlpatterns = [
    path("config", PortalConfigView.as_view(), name="portal-config"),
    *router.urls,
]
