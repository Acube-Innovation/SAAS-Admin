from django.conf import settings
from django.http import JsonResponse
from django.utils.translation import gettext as _

from apps.core.models import Tenant

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _forbidden(code, detail):
    return JsonResponse({"code": code, "detail": detail, "fields": {}}, status=403)


class TenantWriteGuardMiddleware:
    """
    Read-only tenant access (Workflow 3.2 and 3.3): writes in a tenant are refused while the
    tenant is suspended or the request comes through a support session. Auth and admin APIs are
    exempt (settings.TENANT_GUARD_EXEMPT_PATHS).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_id = getattr(request, "tenant_id", None)
        if (
            tenant_id
            and request.method not in SAFE_METHODS
            and not request.path.startswith(settings.TENANT_GUARD_EXEMPT_PATHS)
        ):
            if getattr(request, "support_session_id", None):
                return _forbidden("support_read_only", _("Support access is read-only."))
            if Tenant.objects.filter(pk=tenant_id, status=Tenant.Status.SUSPENDED).exists():
                return _forbidden(
                    "tenant_suspended", _("This account is suspended. Changes are not allowed.")
                )
        return self.get_response(request)
