from django.conf import settings
from django.utils.module_loading import import_string

from .tenancy import db_context


class TenantMiddleware:
    """
    Resolves the request's tenant with settings.TENANCY_RESOLVER and runs the rest of the request
    inside db_context(): one transaction with app.tenant_id and app.user_id set.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = import_string(settings.TENANCY_RESOLVER)(request)
        request.tenant_id = getattr(tenant, "pk", tenant)
        user = getattr(request, "user", None)
        user_id = user.pk if user is not None and user.is_authenticated else None
        with db_context(tenant=tenant, user=user_id):
            return self.get_response(request)
