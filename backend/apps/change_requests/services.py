"""
Workflow 3.6: open -> in_progress -> done / declined. Clients raise and comment; platform staff
assign, start work, reply, link a draft version and decline. A request closes as done when its
linked version goes live. Every change is audited.
"""

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.exceptions import ApiError
from apps.core.tenancy import tenant_context
from apps.messaging_templates.services import OPEN_STATUSES

from .models import ChangeRequest, ChangeRequestComment

S = ChangeRequest.Status


def _locked(change_request):
    return ChangeRequest.objects.select_for_update(of=("self",)).get(pk=change_request.pk)


def _open_only(change_request):
    if change_request.is_closed:
        raise ApiError(
            "request_closed",
            f"This request is {change_request.get_status_display().lower()}.",
            409,
        )


def _update(change_request, actor, action=Action.UPDATE, **changes):
    """Apply `changes` under a row lock and audit them. Returns the saved request."""
    with tenant_context(change_request.tenant_id), transaction.atomic():
        locked = _locked(change_request)
        _open_only(locked)
        before = snapshot(locked)
        for field, value in changes.items():
            setattr(locked, field, value)
        locked.save()
        write_audit(action, locked, before=before, after=snapshot(locked), actor=actor)
        return locked


def raise_request(tenant, user, data) -> ChangeRequest:
    from .tasks import notify_staff

    with tenant_context(tenant), transaction.atomic():
        change_request = ChangeRequest.objects.create(
            tenant=tenant,
            requested_by=user,
            request_type=data["request_type"],
            subject=data["subject"].strip(),
            description=data.get("description", "").strip(),
        )
        write_audit(Action.CREATE, change_request, after=snapshot(change_request), actor=user)
        tenant_id, pk = tenant.pk, change_request.pk
        transaction.on_commit(lambda: notify_staff.delay(tenant_id=tenant_id, request_id=pk))
        return change_request


def add_comment(change_request, user, body, from_staff) -> ChangeRequestComment:
    with tenant_context(change_request.tenant_id), transaction.atomic():
        locked = _locked(change_request)
        _open_only(locked)
        comment = ChangeRequestComment.objects.create(
            tenant_id=locked.tenant_id,
            change_request=locked,
            author=user,
            from_staff=from_staff,
            body=body.strip(),
        )
        locked.save(update_fields=["updated_at"])
        write_audit(Action.CREATE, comment, after=snapshot(comment), actor=user)
        return comment


def assign(change_request, actor, assignee: User | None) -> ChangeRequest:
    if assignee is not None and not (assignee.is_active and assignee.is_platform_staff):
        raise ApiError("validation_error", "Assign the request to platform staff.", 400)
    return _update(change_request, actor, assignee=assignee)


def start_work(change_request, actor) -> ChangeRequest:
    if change_request.status != S.OPEN:
        raise ApiError("invalid_transition", "Only an open request can be started.", 409)
    return _update(
        change_request,
        actor,
        Action.STATUS_CHANGE,
        status=S.IN_PROGRESS,
        started_at=timezone.now(),
        assignee=change_request.assignee or actor,
    )


def decline(change_request, actor, reason) -> ChangeRequest:
    return _update(
        change_request,
        actor,
        Action.STATUS_CHANGE,
        status=S.DECLINED,
        decline_reason=reason.strip(),
        closed_at=timezone.now(),
    )


def link_version(change_request, actor, version) -> ChangeRequest:
    """Link a template version still in progress (draft up to submitted) of the same client."""
    if version.tenant_id != change_request.tenant_id:
        raise ApiError("validation_error", "Pick a version of this client's templates.", 400)
    if version.status not in OPEN_STATUSES:
        raise ApiError(
            "validation_error", "Link a version that is still in progress (not yet live).", 400
        )
    return _update(change_request, actor, linked_version=version)


def close_for_live_version(sender, version, actor, **kwargs):
    """version_went_live receiver: requests linked to the version are done (inside make_live)."""
    requests = ChangeRequest.objects.filter(
        tenant_id=version.tenant_id, linked_version=version, status__in=[S.OPEN, S.IN_PROGRESS]
    ).select_for_update()
    now = timezone.now()
    for change_request in requests:
        before = snapshot(change_request)
        change_request.status = S.DONE
        change_request.closed_at = now
        change_request.save()
        write_audit(
            Action.STATUS_CHANGE,
            change_request,
            before=before,
            after={**snapshot(change_request), "reason": "linked_version_live"},
            actor=actor,
        )
