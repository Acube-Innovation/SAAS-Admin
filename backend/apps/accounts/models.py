import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel
from apps.core.tenancy import user_context


class PlatformRole(models.TextChoices):
    SUPER_ADMIN = "super_admin", _("Super admin")
    SUPPORT = "support", _("Support")


class TenantRole(models.TextChoices):
    OWNER = "owner", _("Owner")
    MANAGER = "manager", _("Manager")
    OFFICE_STAFF = "office_staff", _("Office staff")


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Users need an email address")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("platform_role", PlatformRole.SUPER_ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """A person who signs in. Not tenant-scoped: tenant access comes from Membership rows."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=13, blank=True)
    platform_role = models.CharField(
        max_length=20, choices=PlatformRole.choices, blank=True, default=""
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Can use the Django admin site.")
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci")]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower()
        super().save(*args, **kwargs)

    @property
    def is_platform_staff(self):
        return self.platform_role in (PlatformRole.SUPER_ADMIN, PlatformRole.SUPPORT)

    def is_locked(self, now=None):
        return self.locked_until is not None and self.locked_until > (now or timezone.now())

    def memberships(self):
        """This user's memberships in every tenant (allowed by the membership RLS policy)."""
        with user_context(self):
            return list(Membership.objects.filter(user=self).select_related("tenant"))

    def requires_2fa(self):
        """2FA is mandatory for platform staff and tenant owners (spec Section 1)."""
        if self.is_platform_staff:
            return True
        return any(
            m.role == TenantRole.OWNER and m.status != Membership.Status.DEACTIVATED
            for m in self.memberships()
        )


class Membership(TenantScopedModel):
    class Status(models.TextChoices):
        INVITED = "invited", _("Invited")
        ACTIVE = "active", _("Active")
        DEACTIVATED = "deactivated", _("Deactivated")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    role = models.CharField(max_length=20, choices=TenantRole.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INVITED)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "user"], name="accounts_membership_unique")
        ]

    def __str__(self):
        return f"{self.user_id} {self.role} @ {self.tenant_id}"


class Invite(TenantScopedModel):
    """Single-use invite. Only the SHA-256 hash of the token is stored."""

    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="invites")
    token_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "token_hash"], name="accounts_invite_token")
        ]
