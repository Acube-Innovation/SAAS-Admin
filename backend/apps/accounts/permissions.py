from rest_framework.permissions import BasePermission

from .models import Membership, PlatformRole


def otp_verified(request):
    is_verified = getattr(request.user, "is_verified", None)
    return bool(is_verified and is_verified())


class TwoFactorIfRequired(BasePermission):
    """
    Default for every API view: signed in, and 2FA completed when the user needs it
    (platform staff and tenant owners). Auth endpoints opt out explicitly.
    """

    message = "Two-factor authentication is required."
    code = "otp_required"

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return otp_verified(request) or not user.requires_2fa()


class IsPlatformStaff(BasePermission):
    """Platform staff (super_admin or support) with a 2FA-verified session. For /api/admin/."""

    message = "Platform staff with completed two-factor authentication only."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.is_platform_staff and otp_verified(request)
        )


class IsSuperAdmin(IsPlatformStaff):
    message = "Super admins with completed two-factor authentication only."

    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and request.user.platform_role == PlatformRole.SUPER_ADMIN
        )


class HasTenantRole(TwoFactorIfRequired):
    """
    The user has an active membership in the request's tenant with one of `allowed_roles`, and has
    completed 2FA if they need it (built in, so a view listing only this class keeps the 2FA gate).
    Use via tenant_roles("owner", "manager").
    """

    allowed_roles: tuple[str, ...] = ()
    message = "Your role does not allow this action."
    code = "permission_denied"

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            self.message, self.code = TwoFactorIfRequired.message, TwoFactorIfRequired.code
            return False
        tenant_id = getattr(request, "tenant_id", None)
        if not tenant_id:
            return False
        return Membership.objects.filter(
            user=request.user,
            tenant_id=tenant_id,
            status=Membership.Status.ACTIVE,
            role__in=self.allowed_roles,
        ).exists()


def tenant_roles(*roles):
    return type("HasTenantRole", (HasTenantRole,), {"allowed_roles": tuple(roles)})
