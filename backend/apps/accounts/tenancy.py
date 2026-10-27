import uuid

from apps.core.tenancy import user_context

from .models import Membership

ACTIVE_TENANT_SESSION_KEY = "active_tenant_id"


def active_tenant_ids(user):
    with user_context(user):
        return set(
            Membership.objects.filter(user=user, status=Membership.Status.ACTIVE).values_list(
                "tenant_id", flat=True
            )
        )


def resolve_tenant_from_membership(request):
    """
    The request's tenant: the X-Tenant-ID header or the session's active tenant, if the user has an
    active membership there; otherwise their only active membership. Platform staff get a tenant
    through support sessions (Step 5).
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    tenant_ids = active_tenant_ids(user)
    requested = request.headers.get("X-Tenant-ID") or request.session.get(ACTIVE_TENANT_SESSION_KEY)
    if requested:
        try:
            requested_id = uuid.UUID(str(requested))
        except ValueError:
            return None
        return requested_id if requested_id in tenant_ids else None
    return next(iter(tenant_ids)) if len(tenant_ids) == 1 else None
