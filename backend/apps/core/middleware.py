from django.conf import settings
from django.utils.module_loading import import_string

from .tenancy import db_context


class TenantMiddleware:
    """
    Resolves the request's tenant with settings.TENANCY_RESOLVER and runs the rest of the request
    inside db_context(): one transaction with app.tenant_id, app.user_id and (for 2FA-verified
    platform staff) app.platform_access set.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = import_string(settings.TENANCY_RESOLVER)(request)
        request.tenant_id = getattr(tenant, "pk", tenant)
        user = getattr(request, "user", None)
        signed_in = user is not None and user.is_authenticated
        is_verified = getattr(user, "is_verified", None)
        platform = bool(signed_in and user.is_platform_staff and is_verified and is_verified())
        with db_context(tenant=tenant, user=user.pk if signed_in else None, platform=platform):
            return self.get_response(request)
