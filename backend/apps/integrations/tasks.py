from celery import shared_task

from apps.core.models import Tenant
from apps.core.tenancy import TenantTask, tenant_context


@shared_task(base=TenantTask)
def deliver(tenant_id, delivery_id):
    """One webhook delivery attempt; a failure re-queues itself with back-off (see webhooks.py)."""
    from .webhooks import attempt

    return attempt(delivery_id)


@shared_task
def deliver_due():
    """Celery Beat, every minute: pick up deliveries whose retry is due (e.g. after a restart)."""
    from django.utils import timezone

    from .models import WebhookDelivery
    from .webhooks import attempt

    sent = 0
    for tenant in Tenant.objects.all():
        with tenant_context(tenant):
            due = list(
                WebhookDelivery.objects.filter(
                    tenant=tenant, status="pending", next_attempt_at__lte=timezone.now()
                ).values_list("pk", flat=True)[:100]
            )
        for delivery_id in due:
            with tenant_context(tenant):
                attempt(delivery_id)
            sent += 1
    return sent
