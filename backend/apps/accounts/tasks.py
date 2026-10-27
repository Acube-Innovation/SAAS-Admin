from celery import shared_task
from django.core.mail import send_mail
from django.utils.translation import gettext as _

from apps.core.tenancy import TenantTask

from .models import Invite


@shared_task(base=TenantTask)
def send_invite_email(tenant_id, invite_id, token):
    from .services import invite_url

    invite = Invite.objects.select_related("membership__user", "membership__tenant").get(
        pk=invite_id
    )
    membership = invite.membership
    send_mail(
        subject=_("You're invited to %(tenant)s") % {"tenant": membership.tenant.name},
        message=_(
            "You have been invited to %(tenant)s as %(role)s.\n\n"
            "Accept the invite within 72 hours:\n%(url)s\n"
        )
        % {
            "tenant": membership.tenant.name,
            "role": membership.get_role_display(),
            "url": invite_url(token),
        },
        from_email=None,
        recipient_list=[membership.user.email],
    )
