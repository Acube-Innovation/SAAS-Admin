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
