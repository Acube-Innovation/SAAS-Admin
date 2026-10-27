"""Who is acting and from where, for the request or task being handled."""

import contextvars
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class RequestMeta:
    actor_id: object = None
    actor_email: str = ""
    ip_address: str | None = None
    user_agent: str = ""
    support_session_id: object = None


_current_meta: contextvars.ContextVar[RequestMeta | None] = contextvars.ContextVar(
    "audit_request_meta", default=None
)


def get_request_meta() -> RequestMeta:
    return _current_meta.get() or RequestMeta()


def client_ip(request) -> str | None:
    if settings.AUDIT_TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def meta_from_request(request) -> RequestMeta:
    user = getattr(request, "user", None)
    signed_in = user is not None and user.is_authenticated
    return RequestMeta(
        actor_id=user.pk if signed_in else None,
        actor_email=user.email if signed_in else "",
        ip_address=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        support_session_id=getattr(request, "support_session_id", None),
    )


class AuditContextMiddleware:
    """Records the request's actor, IP and user agent for write_audit() calls during the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_meta.set(meta_from_request(request))
        try:
            return self.get_response(request)
        finally:
            _current_meta.reset(token)


def refresh_actor(user):
    """Call after login/logout inside a request so later entries name the new actor."""
    meta = get_request_meta()
    _current_meta.set(
        RequestMeta(
            actor_id=user.pk if user is not None else None,
            actor_email=user.email if user is not None else "",
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            support_session_id=meta.support_session_id,
        )
    )
