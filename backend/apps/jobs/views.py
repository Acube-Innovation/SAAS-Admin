from drf_spectacular.utils import extend_schema
from rest_framework import viewsets

from apps.accounts.permissions import tenant_roles
from apps.audit.mixins import AuditedModelMixin
from apps.audit.models import Action
from apps.core.portal import PortalViewMixin

from .models import StaffMember
from .serializers import StaffMemberSerializer


@extend_schema(tags=["portal: areas and staff"])
class StaffMemberViewSet(AuditedModelMixin, PortalViewMixin, viewsets.ModelViewSet):
    """Delivery staff (portal spec 2.7). Owner and manager; no delete (deactivate instead)."""

    serializer_class = StaffMemberSerializer
    permission_classes = [tenant_roles("owner", "manager")]
    http_method_names = ["get", "post", "patch", "head", "options"]
    audit_status_fields = ("is_active",)

    def get_queryset(self):
        queryset = self.scoped(StaffMember.objects.all())
        if self.request.query_params.get("active") == "true":
            queryset = queryset.filter(is_active=True)
        return queryset

    def perform_create(self, serializer):
        serializer.save(tenant_id=self.request.tenant_id)
        self.audit(
            Action.CREATE, serializer.instance, after=self.audit_snapshot(serializer.instance)
        )
