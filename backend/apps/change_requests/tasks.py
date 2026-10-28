from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.core.tenancy import TenantTask

from .models import ChangeRequest


@shared_task(base=TenantTask)
def notify_staff(tenant_id, request_id):
    """Workflow 3.6 step 1: tell platform staff a client raised a change request."""
    change_request = ChangeRequest.objects.select_related("tenant").get(pk=request_id)
    recipients = list(
        User.objects.filter(is_active=True, platform_role__in=["super_admin", "support"])
        .order_by("email")
        .values_list("email", flat=True)
    )
    if not recipients:
        return
    url = f"{settings.ADMIN_BASE_URL.rstrip('/')}/change-requests/{change_request.pk}"
    send_mail(
        subject=_("New change request from %(tenant)s") % {"tenant": change_request.tenant.name},
        message=_("%(type)s: %(subject)s\n\nOpen it in the admin console:\n%(url)s\n")
        % {
            "type": change_request.get_request_type_display(),
            "subject": change_request.subject,
            "url": url,
        },
        from_email=None,
        recipient_list=recipients,
    )
