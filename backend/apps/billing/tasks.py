from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils.translation import gettext as _

from apps.accounts.models import Membership
from apps.core.models import Tenant
from apps.core.tenancy import TenantTask

from .models import Wallet
from .pdf import rupees
from .services import low_balance_level


@shared_task(base=TenantTask)
def notify_low_balance(tenant_id):
    """Workflow 3.4 step 2: tell the owners the wallet is below the low-balance level."""
    tenant = Tenant.objects.get(pk=tenant_id)
    wallet = Wallet.objects.get(tenant=tenant)
    owners = list(
        Membership.objects.filter(tenant=tenant, role="owner", status="active")
        .select_related("user")
        .values_list("user__email", flat=True)
    )
    if not owners:
        return
    send_mail(
        subject=_("Wallet balance is low for %(tenant)s") % {"tenant": tenant.name},
        message=_(
            "Your wallet balance is %(balance)s, below the low-balance level of %(level)s.\n"
            "Reminders stop when the balance runs out. Top up here:\n%(url)s\n"
        )
        % {
            "balance": rupees(wallet.balance_paise),
            "level": rupees(low_balance_level(tenant)),
            "url": f"{settings.PORTAL_BASE_URL.rstrip('/')}/wallet",
        },
        from_email=None,
        recipient_list=owners,
    )


@shared_task(base=TenantTask)
def auto_top_up(tenant_id):
    """Start an auto top-up debit if the wallet is low and a mandate is active."""
    from .payment_services import trigger_auto_top_up

    return trigger_auto_top_up(Tenant.objects.get(pk=tenant_id))


@shared_task
def check_auto_top_ups():
    """Celery Beat, every 15 minutes: catch low wallets whose earlier debit failed or timed out."""
    from apps.core.tenancy import tenant_context

    from .payment_services import trigger_auto_top_up

    started = 0
    for tenant in Tenant.objects.filter(status=Tenant.Status.ACTIVE):
        with tenant_context(tenant):
            if trigger_auto_top_up(tenant) == "charge_started":
                started += 1
    return started


@shared_task
def apply_billing_dates():
    """Celery Beat, hourly: apply plan changes and start new periods on each billing date."""
    from apps.core.tenancy import tenant_context

    from .plans import apply_billing_date

    applied = 0
    for tenant in Tenant.objects.all():
        with tenant_context(tenant):
            if apply_billing_date(tenant) != "not_due":
                applied += 1
    return applied
