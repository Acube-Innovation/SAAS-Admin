"""Public API: /api/v1/."""

from rest_framework.routers import SimpleRouter

from .public import PublicOutcomeViewSet, PublicScheduleViewSet, PublicSubscriberViewSet

router = SimpleRouter(trailing_slash=False)
router.register("subscribers", PublicSubscriberViewSet, basename="v1-subscriber")
router.register("schedules", PublicScheduleViewSet, basename="v1-schedule")
router.register("outcomes", PublicOutcomeViewSet, basename="v1-outcome")

urlpatterns = router.urls
