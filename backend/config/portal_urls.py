"""Client portal API (portal spec Section 7): /api/portal/, scoped to the request's tenant."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.jobs.views import DeliveriesView, FollowUpViewSet, JobViewSet, StaffMemberViewSet
from apps.schedules.views import BookingViewSet
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
router.register("bookings", BookingViewSet, basename="portal-booking")
router.register("jobs", JobViewSet, basename="portal-job")
router.register("follow-ups", FollowUpViewSet, basename="portal-follow-up")

urlpatterns = [
    path("config", PortalConfigView.as_view(), name="portal-config"),
    path("deliveries", DeliveriesView.as_view(), name="portal-deliveries"),
    *router.urls,
]
