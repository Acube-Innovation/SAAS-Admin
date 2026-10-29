"""Client portal API (portal spec Section 7): /api/portal/, scoped to the request's tenant."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.billing.views import (
    AutoTopUpActionView,
    AutoTopUpView,
    InvoiceViewSet,
    PlanChangeRequestView,
    PlanView,
    SimulatePaymentView,
    TopUpDetailView,
    TopUpView,
    WalletTransactionViewSet,
    WalletView,
)
from apps.change_requests.views import PortalChangeRequestViewSet
from apps.content_view.views import (
    ContentTemplateViewSet,
    ContentVoiceScriptViewSet,
    QuestionFlowView,
)
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
from apps.reports.views import (
    CommunicationLogViewSet,
    FollowUpAgeingView,
    ReminderResultsView,
    ReportExportView,
    SubscriberHealthView,
)
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
router.register("content/templates", ContentTemplateViewSet, basename="portal-content-template")
router.register(
    "content/voice-scripts", ContentVoiceScriptViewSet, basename="portal-content-voice-script"
)
router.register("communication-log", CommunicationLogViewSet, basename="portal-communication-log")
router.register("wallet/transactions", WalletTransactionViewSet, basename="portal-wallet-txn")
router.register("invoices", InvoiceViewSet, basename="portal-invoice")
router.register("change-requests", PortalChangeRequestViewSet, basename="portal-change-request")

urlpatterns = [
    path("config", PortalConfigView.as_view(), name="portal-config"),
    path("home", HomeView.as_view(), name="portal-home"),
    path("checklist", ChecklistView.as_view(), name="portal-checklist"),
    path("agency", AgencyView.as_view(), name="portal-agency"),
    path("reminder-settings", ReminderSettingsView.as_view(), name="portal-reminder-settings"),
    path("wallet", WalletView.as_view(), name="portal-wallet"),
    path("top-ups", TopUpView.as_view(), name="portal-top-ups"),
    path("top-ups/<uuid:pk>", TopUpDetailView.as_view(), name="portal-top-up"),
    path("auto-top-up", AutoTopUpView.as_view(), name="portal-auto-top-up"),
    path(
        "auto-top-up/<slug:action>", AutoTopUpActionView.as_view(), name="portal-auto-top-up-action"
    ),
    path("plan", PlanView.as_view(), name="portal-plan"),
    path("plan/request-change", PlanChangeRequestView.as_view(), name="portal-plan-change"),
    path("dev/payments/simulate", SimulatePaymentView.as_view(), name="portal-dev-payments"),
    path("deliveries", DeliveriesView.as_view(), name="portal-deliveries"),
    path("deliveries/printed", DeliveriesPrintedView.as_view(), name="portal-deliveries-printed"),
    path("deliveries/export", DeliveriesExportView.as_view(), name="portal-deliveries-export"),
    path("content/question-flows", QuestionFlowView.as_view(), name="portal-question-flows"),
    path("reports/reminder-results", ReminderResultsView.as_view(), name="portal-report-results"),
    path("reports/follow-up-ageing", FollowUpAgeingView.as_view(), name="portal-report-ageing"),
    path("reports/subscriber-health", SubscriberHealthView.as_view(), name="portal-report-health"),
    path("reports/<slug:report>/export", ReportExportView.as_view(), name="portal-report-export"),
    *router.urls,
]
