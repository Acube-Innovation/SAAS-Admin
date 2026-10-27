from celery import shared_task

from apps.core.tenancy import TenantTask

from .models import Note


@shared_task(base=TenantTask)
def count_notes(tenant_id):
    return Note.objects.count()
