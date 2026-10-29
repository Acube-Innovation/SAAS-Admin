"""
Plans, price list, per-client price overrides and plan changes (admin spec 2.11, Workflow 3.8).
Every change is audited. Plan changes wait for the next billing date; a scheduled task applies them,
issues the period's invoice and tells the client.
"""

import datetime as dt
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import Membership
from apps.audit.models import Action
from apps.audit.services import snapshot, write_audit
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.tenancy import tenant_context

from . import services
from .models import DEFAULT_PRICES, Channel, Invoice, Plan, PriceList, PriceOverride, Subscription
from .payment_services import _add_month, subscription_for

logger = logging.getLogger(__name__)
PLAN_FIELDS = ("name", "monthly_fee_paise", "max_active_subscribers", "features", "is_active")


def _today():
    return timezone.localdate()


# --- plans ------------------------------------------------------------------------------------


def create_plan(data, actor) -> Plan:
    try:
        with transaction.atomic():
            plan = Plan.objects.create(**data)
    except IntegrityError as error:
        raise ApiError("code_taken", "A plan with this code already exists.", 409) from error
    write_audit(Action.CREATE, plan, after=snapshot(plan), actor=actor, tenant=None)
    return plan


def update_plan(plan, data, actor) -> Plan:
    """Edit a plan (its code never changes). An edited placeholder becomes a real plan."""
    with transaction.atomic():
        locked = Plan.objects.select_for_update().get(pk=plan.pk)
        before = snapshot(locked)
        for field in PLAN_FIELDS:
            if field in data:
                setattr(locked, field, data[field])
        locked.is_placeholder = False
        locked.save()
        write_audit(
            Action.UPDATE, locked, before=before, after=snapshot(locked), actor=actor, tenant=None
        )
        return locked


# --- price list -------------------------------------------------------------------------------


def add_price(channel, price_paise, effective_from, actor) -> PriceList:
    """A new platform price from a day: today or later (no backdating)."""
    if effective_from < _today():
        raise ApiError("validation_error", "Prices can't start in the past.", 400)
    try:
        with transaction.atomic():
            entry = PriceList.objects.create(
                channel=channel, price_paise=price_paise, effective_from=effective_from
            )
    except IntegrityError as error:
        raise ApiError(
            "price_exists", "This channel already has a price from that day.", 409
        ) from error
    write_audit(Action.CREATE, entry, after=snapshot(entry), actor=actor, tenant=None)
    return entry


def delete_price(entry, actor):
    """Only a price that hasn't started yet can be withdrawn."""
    if entry.effective_from <= _today():
        raise ApiError("price_in_use", "A price already in effect can't be deleted.", 409)
    before = snapshot(entry)
    entry.delete()
    write_audit(
        Action.DELETE,
        object_type="billing.pricelist",
        object_id=str(before["id"]),
        before=before,
        actor=actor,
        tenant=None,
    )


def current_prices(on=None) -> dict:
    """The platform price per channel on a day (no client overrides)."""
    on = on or _today()
    return {channel: _list_price(channel, on) for channel in Channel.values}


def _list_price(channel, on):
    entry = (
        PriceList.objects.filter(channel=channel, effective_from__lte=on)
        .order_by("-effective_from")
        .first()
    )
    return entry.price_paise if entry else DEFAULT_PRICES[channel]


# --- per-client overrides ---------------------------------------------------------------------


def _overlapping(tenant, channel, starts_on, ends_on, exclude=None):
    queryset = PriceOverride.objects.filter(tenant=tenant, channel=channel).filter(
        Q(ends_on__isnull=True) | Q(ends_on__gte=starts_on)
    )
    if ends_on is not None:
        queryset = queryset.filter(starts_on__lte=ends_on)
    if exclude is not None:
        queryset = queryset.exclude(pk=exclude.pk)
    return queryset.exists()


def _lock_tenant(tenant):
    """Serialise override changes for one client (overlap checks need it)."""
    return Tenant.objects.select_for_update().get(pk=tenant.pk)


def add_override(tenant, data, actor) -> PriceOverride:
    starts_on, ends_on = data["starts_on"], data.get("ends_on")
    if starts_on < _today():
        raise ApiError("validation_error", "An override can't start in the past.", 400)
    if ends_on is not None and ends_on < starts_on:
        raise ApiError("validation_error", "The end date must be on or after the start date.", 400)
    with tenant_context(tenant), transaction.atomic():
        _lock_tenant(tenant)
        if _overlapping(tenant, data["channel"], starts_on, ends_on):
            raise ApiError(
                "override_overlap", "This client already has an override for these dates.", 409
            )
        override = PriceOverride.objects.create(
            tenant=tenant,
            channel=data["channel"],
            price_paise=data["price_paise"],
            starts_on=starts_on,
            ends_on=ends_on,
            created_by=actor,
        )
        write_audit(Action.CREATE, override, after=snapshot(override), actor=actor)
        return override


def end_override(override, ends_on, actor) -> PriceOverride:
    """Set or bring forward the end date (today or later, not before the start)."""
    if ends_on is not None and ends_on < max(_today(), override.starts_on):
        raise ApiError(
            "validation_error", "The end date can't be in the past or before the start.", 400
        )
    with tenant_context(override.tenant_id), transaction.atomic():
        _lock_tenant(override.tenant)
        locked = PriceOverride.objects.select_for_update().get(pk=override.pk)
        if locked.ends_on is not None and locked.ends_on < _today():
            raise ApiError("override_ended", "This override has already ended.", 409)
        if _overlapping(locked.tenant, locked.channel, locked.starts_on, ends_on, exclude=locked):
            raise ApiError("override_overlap", "That end date would overlap another override.", 409)
        before = snapshot(locked)
        locked.ends_on = ends_on
        locked.save()
        write_audit(Action.UPDATE, locked, before=before, after=snapshot(locked), actor=actor)
        return locked


def delete_override(override, actor):
    """Only an override that hasn't started can be deleted; a running one is ended instead."""
    if override.starts_on <= _today():
        raise ApiError(
            "override_started", "This override has started; set an end date instead.", 409
        )
    with tenant_context(override.tenant_id), transaction.atomic():
        before = snapshot(override)
        PriceOverride.objects.filter(pk=override.pk).delete()
        write_audit(
            Action.DELETE,
            object_type="billing.priceoverride",
            object_id=str(override.pk),
            before=before,
            actor=actor,
            tenant=override.tenant,
        )


# --- plan changes (Workflow 3.8) --------------------------------------------------------------


def schedule_plan_change(tenant, plan, actor) -> Subscription:
    """Save the new plan as pending; it applies on the next billing date."""
    if not plan.is_active:
        raise ApiError("validation_error", "Pick an active plan.", 400)
    subscription_for(tenant)
    with tenant_context(tenant), transaction.atomic():
        locked = Subscription.objects.select_for_update().get(tenant=tenant)
        if locked.plan_id == plan.pk:
            raise ApiError("validation_error", "The client is already on this plan.", 400)
        before = snapshot(locked)
        locked.pending_plan = plan
        locked.save()
        write_audit(
            Action.UPDATE,
            locked,
            before=before,
            after={**snapshot(locked), "effective_on": locked.next_billing_date.isoformat()},
            actor=actor,
        )
        return locked


def cancel_plan_change(tenant, actor) -> Subscription:
    subscription_for(tenant)
    with tenant_context(tenant), transaction.atomic():
        locked = Subscription.objects.select_for_update().get(tenant=tenant)
        if locked.pending_plan_id is None:
            raise ApiError("no_pending_change", "There is no plan change to cancel.", 409)
        before = snapshot(locked)
        locked.pending_plan = None
        locked.save()
        write_audit(Action.UPDATE, locked, before=before, after=snapshot(locked), actor=actor)
        return locked


def apply_billing_date(tenant, today=None) -> str:
    """
    On (or after) the billing date: apply a pending plan change, start the next period and issue
    the period's invoice; tell the client about a plan change. Runs once per period (the dates
    move inside the same transaction as the invoice).
    """
    today = today or _today()
    with tenant_context(tenant), transaction.atomic():
        subscription = (
            Subscription.objects.select_for_update(of=("self",))
            .select_related("plan", "pending_plan")
            .filter(tenant=tenant, status=Subscription.Status.ACTIVE, next_billing_date__lte=today)
            .first()
        )
        if subscription is None:
            return "not_due"
        before = snapshot(subscription)
        changed_from = None
        if subscription.pending_plan_id:
            changed_from = subscription.plan
            subscription.plan = subscription.pending_plan
            subscription.pending_plan = None
        start = subscription.next_billing_date
        end = _add_month(start) - dt.timedelta(days=1)
        subscription.current_period_start = start
        subscription.next_billing_date = _add_month(start)
        subscription.save()
        plan = subscription.plan
        invoice = None
        if plan.monthly_fee_paise > 0:
            invoice = services.issue_invoice(
                tenant,
                Invoice.Kind.SUBSCRIPTION,
                [
                    {
                        "description": f"{plan.name} plan, {start:%d %b %Y} to {end:%d %b %Y}",
                        "unit_paise": plan.monthly_fee_paise,
                    }
                ],
            )
        write_audit(
            Action.UPDATE,
            subscription,
            before=before,
            after={
                **snapshot(subscription),
                "plan_changed_from": str(changed_from.pk) if changed_from else None,
                "invoice": invoice.number if invoice else None,
            },
        )
        if changed_from is not None:
            tenant_id, old, new = tenant.pk, changed_from.name, plan.name
            transaction.on_commit(lambda: _notify_plan_change(tenant_id, old, new, start))
        return "plan_changed" if changed_from else "renewed"


def _notify_plan_change(tenant_id, old_name, new_name, start):
    tenant = Tenant.objects.get(pk=tenant_id)
    with tenant_context(tenant):
        owners = list(
            Membership.objects.filter(tenant=tenant, role="owner", status="active").values_list(
                "user__email", flat=True
            )
        )
    if not owners:
        return
    send_mail(
        subject=_("Your plan is now %(plan)s") % {"plan": new_name},
        message=_(
            "From %(date)s your plan is %(new)s (it was %(old)s). The invoice for this period is "
            "in Wallet and billing:\n%(url)s\n"
        )
        % {
            "date": f"{start:%d %b %Y}",
            "new": new_name,
            "old": old_name,
            "url": f"{settings.PORTAL_BASE_URL.rstrip('/')}/wallet?tab=invoices",
        },
        from_email=None,
        recipient_list=owners,
    )
