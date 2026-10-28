"""
Values for industry template fields (admin spec 2.6, portal spec 2.2): parsing and validation of
custom fields stored in JSONB, shared by the portal API and the CSV import engine.
"""

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.core.validators import normalize_indian_mobile

from .services import tenant_config

TRUE_WORDS = {"yes", "y", "true", "1"}
FALSE_WORDS = {"no", "n", "false", "0"}
# Day first, as written in India; ISO first so 2026-10-28 is never read as day 2026.
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")


class FieldError(ValueError):
    """A value that doesn't fit its field. The message is shown to the user."""


def parse_boolean(raw) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise FieldError(_("Use yes or no."))


def parse_date(raw) -> dt.date:
    if isinstance(raw, dt.date):
        return raw
    text = str(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise FieldError(_("Enter a date as YYYY-MM-DD or DD-MM-YYYY."))


def parse_phone(raw) -> str:
    try:
        return normalize_indian_mobile(str(raw))
    except serializers.ValidationError as exc:
        raise FieldError(str(exc.detail[0])) from None


def parse_number(raw):
    if isinstance(raw, bool):
        raise FieldError(_("Enter a number."))
    if isinstance(raw, int | float):
        number = Decimal(str(raw))
    else:
        text = re.sub(r"[,\s]", "", str(raw))
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise FieldError(_("Enter a number.")) from None
    if not number.is_finite():
        raise FieldError(_("Enter a number."))
    return int(number) if number == number.to_integral_value() else float(number)


def parse_value(field: dict, raw):
    """
    Parse one non-empty value for a field definition (dict from tenant_config). Returns a JSON-safe
    value (dates as ISO strings) or raises FieldError.
    """
    kind = field["field_type"]
    if kind == "text":
        text = str(raw).strip()
        limit = field.get("max_length")
        if limit and len(text) > limit:
            raise FieldError(_("Up to %(limit)s characters.") % {"limit": limit})
        return text
    if kind == "number":
        return parse_number(raw)
    if kind == "date":
        return parse_date(raw).isoformat()
    if kind == "phone":
        return parse_phone(raw)
    if kind == "boolean":
        return parse_boolean(raw)
    if kind == "choice":
        options = field.get("options") or []
        text = str(raw).strip()
        for option in options:
            if text.lower() == str(option).lower():
                return option
        raise FieldError(_("Choose one of: %(options)s.") % {"options": ", ".join(options)})
    raise FieldError(_("Unsupported field type."))


def is_blank(raw) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def custom_field_definitions(tenant, entity="subscriber", schedule_type=None) -> list[dict]:
    """The tenant's active custom (non-core) field definitions for an entity."""
    config = tenant_config(tenant)
    if entity == "subscriber":
        fields = config["subscriber_fields"]
    else:
        match = [st for st in config["schedule_types"] if st["key"] == schedule_type]
        fields = match[0]["fields"] if match else []
    return [field for field in fields if not field["is_core"]]


def clean_custom_fields(definitions, values, existing=None, partial=False) -> dict:
    """
    Validate `values` against custom field definitions and return the merged custom_fields.

    - Keys must be active custom fields; values for fields switched off later are kept as stored.
    - Required fields must have a value unless `partial` and already stored.
    - A blank value clears an optional field.
    Raises serializers.ValidationError({key: [message]}).
    """
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise serializers.ValidationError({"custom_fields": [_("Must be an object.")]})
    by_key = {field["key"]: field for field in definitions}
    result = dict(existing or {})
    errors = {}
    for key, raw in values.items():
        field = by_key.get(key)
        if field is None:
            errors[key] = [_("Unknown field.")]
            continue
        if is_blank(raw):
            result.pop(key, None)
            continue
        try:
            result[key] = parse_value(field, raw)
        except FieldError as exc:
            errors[key] = [str(exc)]
    for key, field in by_key.items():
        if field["required"] and key not in errors and is_blank(result.get(key)):
            if partial and key not in values and key in (existing or {}):
                continue
            errors[key] = [_("This field is required.")]
    if errors:
        raise serializers.ValidationError(errors)
    return result
