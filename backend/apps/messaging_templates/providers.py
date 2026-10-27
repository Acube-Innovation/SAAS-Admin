"""
WhatsApp provider interface (spec 3.4). Phase 2 adds a Meta Cloud API implementation; until then
StubProvider is used everywhere: it makes no network calls and tests (or a developer) drive the
status Meta would report with StubProvider.set_status().
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils.module_loading import import_string

PENDING, APPROVED, REJECTED, PAUSED, DISABLED = (
    "pending",
    "approved",
    "rejected",
    "paused",
    "disabled",
)


@dataclass(frozen=True)
class ProviderStatus:
    status: str  # pending | approved | rejected | paused | disabled
    reason: str = ""


class WhatsAppProvider(ABC):
    @abstractmethod
    def submit_template(self, version) -> str:
        """Submit a signed-off version for review. Returns the provider's template id."""

    @abstractmethod
    def get_template_status(self, provider_template_id: str) -> ProviderStatus:
        """The provider's current review status for a submitted template."""

    @abstractmethod
    def get_quality_rating(self) -> str:
        """Quality rating Meta gives the shared sending number: GREEN, YELLOW or RED (spec 2.2)."""


class StubProvider(WhatsAppProvider):
    """In-process stand-in for Meta. State lives in the Django cache (shared by API and workers)."""

    KEY = "whatsapp-stub:{}"

    def submit_template(self, version) -> str:
        provider_id = f"stub-{uuid.uuid4().hex[:12]}"
        cache.set(
            self.KEY.format(provider_id),
            {
                "status": PENDING,
                "reason": "",
                "name": version.template.name,
                "language": version.template.language,
                "body": version.body,
            },
            timeout=None,
        )
        return provider_id

    def get_template_status(self, provider_template_id: str) -> ProviderStatus:
        data = cache.get(self.KEY.format(provider_template_id))
        if data is None:
            return ProviderStatus(PENDING)
        return ProviderStatus(data["status"], data.get("reason", ""))

    QUALITY_KEY = "whatsapp-stub:quality"

    def get_quality_rating(self) -> str:
        return cache.get(self.QUALITY_KEY, "GREEN")

    # --- test / developer controls ---

    @classmethod
    def set_quality(cls, rating: str):
        cache.set(cls.QUALITY_KEY, rating, timeout=None)

    @classmethod
    def set_status(cls, provider_template_id: str, status: str, reason: str = ""):
        key = cls.KEY.format(provider_template_id)
        data = cache.get(key) or {}
        cache.set(key, {**data, "status": status, "reason": reason}, timeout=None)

    @classmethod
    def submitted(cls, provider_template_id: str) -> dict | None:
        return cache.get(cls.KEY.format(provider_template_id))


def get_provider() -> WhatsAppProvider:
    return import_string(settings.WHATSAPP_PROVIDER)()
