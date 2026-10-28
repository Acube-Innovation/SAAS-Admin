"""
Workflow 3.4: the WhatsApp template version state machine. Every change goes through _transition(),
which checks the allowed source states, writes a TemplateStatusEvent and an audit entry.
"""

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.exceptions import ApiError
from apps.core.tenancy import db_context, tenant_context

from . import providers
from .models import TemplateStatusEvent, VersionStatus, WhatsAppTemplate, WhatsAppTemplateVersion
from .signals import version_went_live
from .validation import validate_content, validate_evidence

S = VersionStatus
ALL_STATUSES = frozenset(S.values)
OPEN_STATUSES = frozenset({S.DRAFT, S.SENT_TO_CLIENT, S.SIGNED_OFF, S.SENT_BACK, S.SUBMITTED})

# action: (allowed source statuses, target status)
TRANSITIONS = {
    "edit": ({S.DRAFT, S.SENT_BACK}, S.DRAFT),
    "send_to_client": ({S.DRAFT}, S.SENT_TO_CLIENT),
    "client_approve": ({S.SENT_TO_CLIENT}, S.SIGNED_OFF),
    "client_send_back": ({S.SENT_TO_CLIENT}, S.SENT_BACK),
    "manual_signoff": ({S.SENT_TO_CLIENT}, S.SIGNED_OFF),
    "submit": ({S.SIGNED_OFF}, S.SUBMITTED),
    "provider_approved": ({S.SUBMITTED}, S.APPROVED),
    "provider_rejected": ({S.SUBMITTED}, S.REJECTED),
    "make_live": ({S.APPROVED}, S.APPROVED),
    "provider_paused": ({S.APPROVED}, S.PAUSED),
    "provider_reenabled": ({S.PAUSED}, S.APPROVED),
    "disable": (ALL_STATUSES - {S.DISABLED}, S.DISABLED),
    "new_version": ({S.APPROVED, S.REJECTED}, S.DRAFT),  # target is the NEW version's status
}

CONTENT_FIELDS = ("header_text", "body", "footer", "buttons", "sample_values")


def _check(version, action):
    allowed, _target = TRANSITIONS[action]
    if version.status not in allowed:
        raise ApiError(
            "invalid_transition",
            f"Can't {action.replace('_', ' ')} a version that is {version.get_status_display()}.",
            409,
        )


def _locked(version):
    return (
        WhatsAppTemplateVersion.objects.select_for_update()
        .select_related("template__schedule_type", "template__tenant")
        .get(pk=version.pk)
    )


AUDIT_ACTIONS = {"create": Action.CREATE, "new_version": Action.CREATE, "edit": Action.UPDATE}


def _record(version, action, from_status, actor, note="", before=None):
    TemplateStatusEvent.objects.create(
        tenant_id=version.tenant_id,
        version=version,
        action=action,
        from_status=from_status,
        to_status=version.status,
        actor=actor,
        note=note,
    )
    write_audit(
        AUDIT_ACTIONS.get(action, Action.STATUS_CHANGE),
        version,
        before=before,
        after={**snapshot(version), "action": action, "note": note},
        actor=actor,
    )


def _transition(version, action, actor, note="", **changes):
    with tenant_context(version.tenant_id), transaction.atomic():
        version = _locked(version)
        _check(version, action)
        before = snapshot(version)
        from_status = version.status
        for field, value in changes.items():
            setattr(version, field, value)
        version.status = TRANSITIONS[action][1]
        version.save()
        _record(version, action, from_status, actor, note, before)
        return version


def _validated_content(schedule_type, data):
    return validate_content(
        schedule_type=schedule_type,
        header_text=data.get("header_text", ""),
        body=data.get("body", ""),
        footer=data.get("footer", ""),
        buttons=data.get("buttons", []),
        sample_values=data.get("sample_values", {}),
    )


# --- create / edit -----------------------------------------------------------------------------


def create_template(tenant, data, actor) -> WhatsAppTemplateVersion:
    """A new template with version 1 as a draft."""
    schedule_type = data["schedule_type"]
    if (
        tenant.industry_template_id is None
        or schedule_type.template_id != tenant.industry_template_id
    ):
        raise ApiError(
            "validation_error", "Pick a schedule type from this client's industry template.", 400
        )
    samples = _validated_content(schedule_type, data)
    with tenant_context(tenant), transaction.atomic():
        if WhatsAppTemplate.objects.filter(
            tenant=tenant, name=data["name"], language=data["language"]
        ).exists():
            raise ApiError(
                "name_taken", "This client already has a template with this name and language.", 409
            )
        template = WhatsAppTemplate.objects.create(
            tenant=tenant,
            name=data["name"],
            language=data["language"],
            category=data.get("category", WhatsAppTemplate.Category.UTILITY),
            schedule_type=schedule_type,
        )
        write_audit(Action.CREATE, template, after=snapshot(template), actor=actor)
        version = WhatsAppTemplateVersion.objects.create(
            tenant=tenant,
            template=template,
            version=1,
            **{
                f: data.get(
                    f, "" if f in ("header_text", "footer") else [] if f == "buttons" else ""
                )
                for f in ("header_text", "body", "footer", "buttons")
            },
            sample_values=samples,
        )
        _record(version, "create", "", actor)
        return version


def edit_version(version, data, actor) -> WhatsAppTemplateVersion:
    """Edit a draft (or a sent-back version, which returns to draft). Approved content is frozen."""
    with tenant_context(version.tenant_id), transaction.atomic():
        locked = _locked(version)
        _check(locked, "edit")
        content = {f: data.get(f, getattr(locked, f)) for f in CONTENT_FIELDS}
        content["sample_values"] = _validated_content(locked.template.schedule_type, content)
        return _transition(locked, "edit", actor, **content)


# --- review and approval -----------------------------------------------------------------------


def send_to_client(version, actor):
    return _transition(version, "send_to_client", actor)


def client_approve(version, actor):
    """The client's owner or manager approves (portal endpoint in Step 18)."""
    return _transition(
        version,
        "client_approve",
        actor,
        signed_off_by=actor,
        signed_off_at=timezone.now(),
        signoff_method="client",
    )


def client_send_back(version, actor, comment):
    if not (5 <= len(comment.strip()) <= 1000):
        raise ApiError("validation_error", "Say what should change (5-1000 characters).", 400)
    return _transition(version, "client_send_back", actor, note=comment.strip())


def manual_signoff(version, actor, evidence, note=""):
    """Super admin records the client's sign-off received outside the portal, with evidence."""
    validate_evidence(evidence)
    with tenant_context(version.tenant_id), transaction.atomic():
        locked = _locked(version)
        _check(locked, "manual_signoff")
        locked.signoff_evidence.save(evidence.name, evidence, save=False)
        return _transition(
            locked,
            "manual_signoff",
            actor,
            note=note,
            signoff_evidence=locked.signoff_evidence.name,
            signed_off_by=actor,
            signed_off_at=timezone.now(),
            signoff_method="manual",
        )


def submit(version, actor):
    """Submit a signed-off version to Meta through the provider."""
    with tenant_context(version.tenant_id), transaction.atomic():
        locked = _locked(version)
        _check(locked, "submit")
        _validated_content(
            locked.template.schedule_type, {f: getattr(locked, f) for f in CONTENT_FIELDS}
        )
        provider_id = providers.get_provider().submit_template(locked)
        return _transition(
            locked, "submit", actor, provider_template_id=provider_id, submitted_at=timezone.now()
        )


def sync_status(version) -> WhatsAppTemplateVersion:
    """Apply the provider's review status (approved, rejected, paused, re-enabled, disabled)."""
    if not version.provider_template_id:
        return version
    result = providers.get_provider().get_template_status(version.provider_template_id)
    action = {
        (S.SUBMITTED, providers.APPROVED): "provider_approved",
        (S.SUBMITTED, providers.REJECTED): "provider_rejected",
        (S.APPROVED, providers.PAUSED): "provider_paused",
        (S.PAUSED, providers.APPROVED): "provider_reenabled",
    }.get((version.status, result.status))
    if action is None and result.status == providers.DISABLED and version.status != S.DISABLED:
        return _transition(version, "disable", None, note="Disabled by the provider", is_live=False)
    if action is None:
        return version
    changes = {}
    if action == "provider_approved":
        changes["approved_at"] = timezone.now()
    if action == "provider_rejected":
        changes["rejection_reason"] = result.reason or "Rejected by the provider"
    return _transition(version, action, None, note=result.reason, **changes)


def sync_all_statuses() -> int:
    """Celery Beat: refresh every version waiting on or approved by the provider."""
    with db_context(platform=True):
        versions = list(
            WhatsAppTemplateVersion.objects.filter(
                status__in=[S.SUBMITTED, S.APPROVED, S.PAUSED]
            ).exclude(provider_template_id="")
        )
    changed = 0
    for version in versions:
        if sync_status(version).status != version.status:
            changed += 1
    return changed


# --- going live --------------------------------------------------------------------------------


def make_live(version, actor):
    """The approved version becomes the one reminders use; the previous live one stays approved."""
    with tenant_context(version.tenant_id), transaction.atomic():
        locked = _locked(version)
        _check(locked, "make_live")
        if locked.is_live:
            raise ApiError("already_live", "This version is already live.", 409)
        WhatsAppTemplateVersion.objects.filter(template=locked.template, is_live=True).update(
            is_live=False
        )
        live = _transition(locked, "make_live", actor, is_live=True)
        version_went_live.send(sender=WhatsAppTemplateVersion, version=live, actor=actor)
        return live


def disable(version, actor, reason=""):
    return _transition(version, "disable", actor, note=reason, is_live=False)


def new_version(version, actor) -> WhatsAppTemplateVersion:
    """Start a new draft from an approved, live or rejected version (its content copied)."""
    with tenant_context(version.tenant_id), transaction.atomic():
        source = _locked(version)
        _check(source, "new_version")
        template = source.template
        if template.versions.filter(status__in=OPEN_STATUSES).exists():
            raise ApiError(
                "open_version_exists", "Finish or disable the version in progress first.", 409
            )
        number = (template.versions.aggregate(n=Max("version"))["n"] or 0) + 1
        draft = WhatsAppTemplateVersion.objects.create(
            tenant_id=source.tenant_id,
            template=template,
            version=number,
            **{f: getattr(source, f) for f in CONTENT_FIELDS},
        )
        _record(draft, "new_version", "", actor, note=f"From version {source.version}")
        return draft
