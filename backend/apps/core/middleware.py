from django.conf import settings
from django.utils.module_loading import import_string

from .tenancy import tenant_context


class TenantMiddleware:
    """
    Resolves the request's tenant with settings.TENANCY_RESOLVER and runs the rest of the request
    inside tenant_context(), i.e. one transaction with app.tenant_id set.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = import_string(settings.TENANCY_RESOLVER)(request)
        request.tenant_id = getattr(tenant, "pk", tenant)
        with tenant_context(tenant):
            return self.get_response(request)
