"""Client portal API (portal spec Section 7): /api/portal/, scoped to the request's tenant."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.jobs.views import (
    DeliveriesExportView,
    DeliveriesPrintedView,
    DeliveriesView,
    FollowUpViewSet,
    JobViewSet,
    StaffMemberViewSet,
)
from apps.portal.team import AgencyView, ReminderSettingsView, TeamViewSet
from apps.portal.views import ChecklistView, HomeView, PortalConfigView
from apps.schedules.views import BookingViewSet
from apps.subscribers.views import (
    AreaViewSet,
    ImportJobViewSet,
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
router.register("users", TeamViewSet, basename="portal-user")

urlpatterns = [
    path("config", PortalConfigView.as_view(), name="portal-config"),
    path("home", HomeView.as_view(), name="portal-home"),
    path("checklist", ChecklistView.as_view(), name="portal-checklist"),
    path("agency", AgencyView.as_view(), name="portal-agency"),
    path("reminder-settings", ReminderSettingsView.as_view(), name="portal-reminder-settings"),
    path("deliveries", DeliveriesView.as_view(), name="portal-deliveries"),
    path("deliveries/printed", DeliveriesPrintedView.as_view(), name="portal-deliveries-printed"),
    path("deliveries/export", DeliveriesExportView.as_view(), name="portal-deliveries-export"),
    *router.urls,
]
