"""Helpers for client portal views (/api/portal/): everything is scoped to the request's tenant."""

from .models import Tenant


class PortalViewMixin:
    """
    `scoped(queryset)` filters to the request's tenant (RLS does too; this keeps queries explicit)
    and adds `tenant` to the serializer context.
    """

    def scoped(self, queryset):
        if getattr(self, "swagger_fake_view", False):  # schema generation has no tenant
            return queryset.none()
        return queryset.filter(tenant_id=self.request.tenant_id)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if getattr(self.request, "tenant_id", None):
            context["tenant"] = Tenant.objects.get(pk=self.request.tenant_id)
        return context
