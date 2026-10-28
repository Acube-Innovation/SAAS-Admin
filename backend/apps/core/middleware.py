from django.conf import settings
from django.http import JsonResponse
from django.utils.module_loading import import_string

from .tenancy import TenantAccessDenied, db_context


class TenantMiddleware:
    """
    Resolves the request's tenant with settings.TENANCY_RESOLVER and runs the rest of the request
    inside db_context(): one transaction with app.tenant_id, app.user_id and (for 2FA-verified
    platform staff) app.platform_access set.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            tenant = import_string(settings.TENANCY_RESOLVER)(request)
        except TenantAccessDenied as exc:
            return JsonResponse({"code": exc.code, "detail": exc.detail, "fields": {}}, status=403)
        request.tenant_id = getattr(tenant, "pk", tenant)
        user = getattr(request, "user", None)
        signed_in = user is not None and user.is_authenticated
        is_verified = getattr(user, "is_verified", None)
        platform = bool(signed_in and user.is_platform_staff and is_verified and is_verified())
        with db_context(tenant=tenant, user=user.pk if signed_in else None, platform=platform):
            return self.get_response(request)


class HealthCheckMiddleware:
    """
    Answers /api/health before host validation. Load balancer health checks reach each task by its
    private IP, which is not (and should not be) in ALLOWED_HOSTS. Must be first in MIDDLEWARE.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        from .views import HealthView

        self.health = HealthView.as_view()

    def __call__(self, request):
        if request.path == "/api/health" and request.method in ("GET", "HEAD"):
            response = self.health(request)
            response.render()
            return response
        return self.get_response(request)
