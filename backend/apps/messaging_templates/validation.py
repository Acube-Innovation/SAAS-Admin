"""Field rules for WhatsApp template content (spec 2.7)."""

import os
import re

from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.industry.models import FieldDefinition

NAME_RE = r"^[a-z0-9_]{1,512}$"
VARIABLE_RE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
BODY_MAX, HEADER_MAX, FOOTER_MAX, BUTTON_MAX, BUTTONS_MAX = 1024, 60, 60, 25, 3
OUTCOMES = ("confirmed", "not_available", "reschedule")
EVIDENCE_MAX_BYTES = 5 * 1024 * 1024
EVIDENCE_TYPES = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}


def allowed_variables(schedule_type) -> set[str]:
    """The schedule type's fields, the template's subscriber fields, and agency_name."""
    keys = FieldDefinition.objects.filter(
        template_id=schedule_type.template_id, is_active=True
    ).filter(schedule_type__isnull=True) | FieldDefinition.objects.filter(
        schedule_type=schedule_type, is_active=True
    )
    return {*keys.values_list("key", flat=True), "agency_name"}


def variables_in(text: str) -> list[str]:
    return VARIABLE_RE.findall(text or "")


def _malformed(text: str) -> bool:
    """Braces left over once valid {{variable}} placeholders are removed."""
    stripped = VARIABLE_RE.sub("", text or "")
    return "{{" in stripped or "}}" in stripped


def validate_content(*, schedule_type, header_text, body, footer, buttons, sample_values):
    """Raise a ValidationError keyed by field for anything that breaks spec 2.7."""
    errors: dict[str, list[str]] = {}
    allowed = allowed_variables(schedule_type)

    def add(field, message):
        errors.setdefault(field, []).append(message)

    for field, text, limit in (
        ("header_text", header_text, HEADER_MAX),
        ("body", body, BODY_MAX),
        ("footer", footer, FOOTER_MAX),
    ):
        if len(text or "") > limit:
            add(
                field, _("At most %(n)d characters (now %(len)d).") % {"n": limit, "len": len(text)}
            )
        if _malformed(text):
            add(field, _("Write variables as {{field_key}}, e.g. {{customer_name}}."))
        unknown = sorted(set(variables_in(text)) - allowed)
        if unknown:
            add(field, _("Unknown variables: %s.") % ", ".join(unknown))
    if not (body or "").strip():
        add("body", _("The body is required."))
    if len(variables_in(header_text)) > 1:
        add("header_text", _("The header can have at most one variable."))
    if variables_in(footer) or "{{" in (footer or ""):
        add("footer", _("The footer can't contain variables."))

    if len(buttons) > BUTTONS_MAX:
        add("buttons", _("At most %d quick-reply buttons.") % BUTTONS_MAX)
    texts = []
    for index, button in enumerate(buttons):
        text = (button.get("text") or "").strip()
        if not text or len(text) > BUTTON_MAX:
            add(
                "buttons",
                _("Button %(i)d: 1-%(n)d characters.") % {"i": index + 1, "n": BUTTON_MAX},
            )
        if button.get("outcome") not in OUTCOMES:
            add("buttons", _("Button %(i)d: choose an outcome.") % {"i": index + 1})
        texts.append(text)
    if len(set(texts)) != len(texts):
        add("buttons", _("Button texts must be unique."))

    used = set(variables_in(header_text)) | set(variables_in(body))
    missing = sorted(v for v in used if not str(sample_values.get(v, "")).strip())
    if missing:
        add("sample_values", _("Give a sample value for: %s.") % ", ".join(missing))

    if errors:
        raise serializers.ValidationError(errors)
    return {k: str(sample_values[k]) for k in sorted(used)}


def validate_evidence(uploaded) -> None:
    """Sign-off evidence: PDF, PNG or JPG, up to 5 MB, and the content must match the type."""
    ext = os.path.splitext(uploaded.name or "")[1].lower()
    if ext not in EVIDENCE_TYPES:
        raise serializers.ValidationError({"evidence": [_("Upload a PDF, PNG or JPG file.")]})
    if uploaded.size > EVIDENCE_MAX_BYTES:
        raise serializers.ValidationError({"evidence": [_("The file must be 5 MB or smaller.")]})
    head = uploaded.read(8)
    uploaded.seek(0)
    if not head.startswith(EVIDENCE_TYPES[ext]):
        raise serializers.ValidationError(
            {"evidence": [_("The file content doesn't match its type.")]}
        )
