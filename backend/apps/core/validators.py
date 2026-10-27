"""Field rules from docs/admin-console-spec.md 2.4: Indian GSTIN, PIN and mobile numbers."""

import re

from django.utils.translation import gettext as _
from rest_framework import serializers

from .constants import GST_STATE_CODES

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PIN_RE = re.compile(r"^[1-9][0-9]{5}$")
MOBILE_RE = re.compile(r"^[6-9][0-9]{9}$")


def normalize_indian_mobile(value: str) -> str:
    """Accept 98765 43210, 09876543210, 919876543210 or +91-98765-43210; return +919876543210."""
    digits = re.sub(r"[\s\-()]", "", value or "")
    if digits.startswith("+91"):
        digits = digits[3:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not MOBILE_RE.match(digits):
        raise serializers.ValidationError(
            _("Enter a valid Indian mobile number (10 digits starting with 6-9).")
        )
    return f"+91{digits}"


def validate_pin_code(value: str) -> str:
    value = (value or "").strip()
    if not PIN_RE.match(value):
        raise serializers.ValidationError(_("Enter a 6-digit PIN code that does not start with 0."))
    return value


def normalize_gstin(value: str | None) -> str | None:
    """Uppercase and check the GSTIN format; empty becomes None (GSTIN is optional)."""
    value = (value or "").strip().upper()
    if not value:
        return None
    if not GSTIN_RE.match(value):
        raise serializers.ValidationError(_("Enter a valid 15-character GSTIN."))
    return value


def check_gstin_state(gstin: str | None, state: str | None) -> None:
    """The first two digits of a GSTIN are the GST code of the state it was issued in."""
    if gstin and state and GST_STATE_CODES.get(state) != gstin[:2]:
        raise serializers.ValidationError(
            {"gstin": [_("This GSTIN does not belong to the selected state.")]}
        )
