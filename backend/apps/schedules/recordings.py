"""
Call recordings (portal spec 2.8). Stored under one prefix so a single S3 lifecycle rule
(infra/terraform, `expire-recordings`) deletes them after RETENTION_DAYS. The voice engine
(Phase 3) writes keys with recording_key(); the portal plays them through 5-minute links.
"""

import datetime as dt

from django.utils import timezone

RECORDINGS_PREFIX = "recordings/"
RETENTION_DAYS = 90
LINK_SECONDS = 300


def recording_key(tenant_id, attempt_id, ext: str = "mp3") -> str:
    return f"{RECORDINGS_PREFIX}{tenant_id}/{attempt_id}.{ext}"


def is_expired(received_at, now=None) -> bool:
    """True once the lifecycle rule has (or will have) deleted the recording."""
    return received_at < (now or timezone.now()) - dt.timedelta(days=RETENTION_DAYS)
