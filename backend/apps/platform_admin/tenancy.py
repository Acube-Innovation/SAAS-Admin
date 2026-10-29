from django.conf import settings

from apps.accounts.permissions import otp_verified
from apps.accounts.tenancy import resolve_tenant_from_membership
from apps.core.tenancy import TenantAccessDenied, db_context

from .models import SupportSession

SUPPORT_SESSION_KEY = "support_session_id"


def _exempt(path):
    return path.startswith(settings.TENANT_GUARD_EXEMPT_PATHS)


def resolve_request_tenant(request):
    """
    Platform staff act in a tenant only through an active support session (Workflow 3.3);
    everyone else through their memberships. Sets request.support_session_id in support mode.
    """
    api_key = getattr(request, "api_key", None)
    if api_key is not None:  # the public API: the key's agency (apps.integrations)
        return api_key.tenant_id
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_platform_staff:
        return resolve_tenant_from_membership(request)

    session_id = request.session.get(SUPPORT_SESSION_KEY)
    if not session_id or not otp_verified(request):
        return None
    with db_context(platform=True):
        session = SupportSession.objects.filter(pk=session_id, staff_user=user).first()
    if session is None or not session.is_active():
        request.session.pop(SUPPORT_SESSION_KEY, None)
        if not _exempt(request.path):
            raise TenantAccessDenied(
                "support_session_expired", "Your support session has ended. Open it again."
            )
        return None
    request.support_session_id = session.pk
    return session.tenant_id
