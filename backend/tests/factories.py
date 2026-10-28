"""Small helpers for building test data (no extra dependencies)."""

import itertools

from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import Membership, User
from apps.core.models import Tenant
from apps.core.tenancy import tenant_context

PASSWORD = "correct-horse-battery-staple"
_seq = itertools.count(1)


def make_tenant(name=None, **extra):
    n = next(_seq)
    fields = {
        "name": name or f"Agency {n}",
        "legal_name": f"{name or f'Agency {n}'} Pvt Ltd",
        "address_line1": "1 Main Road",
        "city": "Kochi",
        "state": "KL",
        "pin_code": "682001",
        "contact_name": "Contact",
        "contact_phone": "+919876543210",
        "contact_email": f"agency{n}@example.com",
    }
    fields.update(extra)
    return Tenant.objects.create(**fields)


def make_user(email=None, password=PASSWORD, **extra):
    return User.objects.create_user(email or f"user{next(_seq)}@example.com", password, **extra)


def add_membership(user, tenant, role, status=Membership.Status.ACTIVE):
    with tenant_context(tenant):
        return Membership.objects.create(user=user, tenant=tenant, role=role, status=status)


def add_totp_device(user, confirmed=True):
    return TOTPDevice.objects.create(user=user, name="Authenticator", confirmed=confirmed)


def totp_code(device):
    code = totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits, drift=0)
    return f"{code:0{device.digits}d}"


def next_id() -> int:
    return next(_seq)
