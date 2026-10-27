from rest_framework import exceptions
from rest_framework.views import exception_handler


class ApiError(exceptions.APIException):
    """An API error with an explicit machine-readable code, e.g. ApiError("invite_expired", ...)."""

    status_code = 400

    def __init__(self, code, detail, status_code=None):
        super().__init__(detail=detail, code=code)
        if status_code is not None:
            self.status_code = status_code


def api_exception_handler(exc, context):
    """Error bodies follow the spec: {"code": "...", "detail": "...", "fields": {...}}."""
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, exceptions.ValidationError):
        detail = exc.detail
        if isinstance(detail, dict):
            fields = detail
            message = "Please correct the highlighted fields."
        else:
            fields = {}
            message = " ".join(str(item) for item in detail) if isinstance(detail, list) else detail
        response.data = {"code": "validation_error", "detail": str(message), "fields": fields}
    else:
        codes = exc.get_codes() if isinstance(exc, exceptions.APIException) else "error"
        code = codes if isinstance(codes, str) else "error"
        detail = getattr(exc, "detail", str(exc))
        response.data = {"code": code, "detail": str(detail), "fields": {}}
    return response
