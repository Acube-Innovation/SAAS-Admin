"""Public API authentication (/api/v1/): `Authorization: Bearer <API key>`, rate-limited per key."""

from django.http import JsonResponse

from . import keys

PUBLIC_PREFIX = "/api/v1/"


def _error(status, code, detail, **headers):
    response = JsonResponse({"code": code, "detail": detail, "fields": {}}, status=status)
    for name, value in headers.items():
        response[name] = value
    return response


class ApiKeyMiddleware:
    """
    Runs before TenantMiddleware. For /api/v1/ it checks the key (revoked keys fail at once) and
    the per-key rate limit, then sets request.api_key so the tenant resolver uses the key's tenant.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.api_key = None
        if not request.path.startswith(PUBLIC_PREFIX):
            return self.get_response(request)
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return _error(
                401, "api_key_required", "Send your API key as: Authorization: Bearer <key>."
            )
        key = keys.authenticate(header.removeprefix("Bearer ").strip())
        if key is None:
            return _error(401, "invalid_api_key", "This API key is not valid or has been revoked.")
        limited, retry_after = keys.over_rate_limit(key)
        if limited:
            return _error(
                429,
                "rate_limited",
                "Too many requests for this key. Slow down.",
                **{"Retry-After": str(retry_after)},
            )
        request.api_key = key
        return self.get_response(request)
