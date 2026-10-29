from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.billing.views import PaymentWebhookView
from apps.core.views import HealthView

urlpatterns = [
    path("api/health", HealthView.as_view(), name="health"),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/admin/", include("apps.platform_admin.urls")),
    path("api/portal/", include("config.portal_urls")),
    path("api/v1/", include("apps.integrations.urls")),
    path(
        "api/payments/webhooks/<slug:provider>",
        PaymentWebhookView.as_view(),
        name="payment-webhook",
    ),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
