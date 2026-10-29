"""
API keys: `sck_<tenant id hex>_<secret>`. The tenant part lets a request be resolved to its tenant
without searching other tenants; only the SHA-256 hash of the whole key is stored. A revoked key is
refused on its very next request (no caching).
"""

import hashlib
import secrets
import uuid

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.exceptions import ApiError
from apps.core.tenancy import tenant_context

from .models import ApiKey

PREFIX = "sck"
LAST_USED_EVERY_SECONDS = 60


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_key(tenant, name: str, user) -> tuple[ApiKey, str]:
    """Returns the key record and the raw key (to show once)."""
    secret = secrets.token_urlsafe(32)
    raw = f"{PREFIX}_{tenant.pk.hex}_{secret}"
    with tenant_context(tenant):
        key = ApiKey.objects.create(
            tenant=tenant,
            name=name.strip(),
            prefix=f"{PREFIX}_…{secret[:6]}",
            key_hash=_hash(raw),
            created_by=user,
        )
        write_audit(Action.CREATE, key, after=snapshot(key, exclude=("key_hash",)), actor=user)
    return key, raw


def revoke_key(key, user) -> ApiKey:
    with tenant_context(key.tenant_id), transaction.atomic():
        locked = ApiKey.objects.select_for_update().get(pk=key.pk)
        if locked.revoked_at:
            raise ApiError("already_revoked", "This key is already revoked.", 409)
        before = snapshot(locked, exclude=("key_hash",))
        locked.revoked_at = timezone.now()
        locked.revoked_by = user
        locked.save()
        write_audit(
            Action.UPDATE,
            locked,
            before=before,
            after=snapshot(locked, exclude=("key_hash",)),
            actor=user,
        )
        return locked


def parse_tenant(raw: str):
    """The tenant id named in a key, or None if the key isn't in our format."""
    parts = raw.split("_", 2)
    if len(parts) != 3 or parts[0] != PREFIX or len(parts[1]) != 32:
        return None
    try:
        return uuid.UUID(hex=parts[1])
    except ValueError:
        return None


def authenticate(raw: str):
    """The live ApiKey for a raw key, or None (unknown, malformed or revoked)."""
    tenant_id = parse_tenant(raw)
    if tenant_id is None:
        return None
    with tenant_context(tenant_id):
        key = ApiKey.objects.filter(key_hash=_hash(raw), revoked_at__isnull=True).first()
        if key is None:
            return None
        now = timezone.now()
        if (
            key.last_used_at is None
            or (now - key.last_used_at).total_seconds() > LAST_USED_EVERY_SECONDS
        ):
            ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
        return key


def over_rate_limit(key) -> tuple[bool, int]:
    """Count this request against the key's per-minute limit. Returns (limited, retry_after)."""
    now = timezone.now()
    bucket = f"apikey-rate:{key.pk}:{now:%Y%m%d%H%M}"
    added = cache.add(bucket, 1, timeout=90)
    count = 1 if added else cache.incr(bucket)
    return count > key.rate_limit_per_minute, 60 - now.second
