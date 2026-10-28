from celery import shared_task

from apps.core.tenancy import TenantTask

from . import importer
from .models import ImportJob


@shared_task(base=TenantTask, name="apps.subscribers.tasks.preview_import")
def preview_import(job_id, tenant_id):
    importer.run_preview(ImportJob.objects.get(pk=job_id))


@shared_task(base=TenantTask, name="apps.subscribers.tasks.confirm_import")
def confirm_import(job_id, tenant_id):
    importer.run_confirm(ImportJob.objects.get(pk=job_id))


@shared_task(name="apps.subscribers.tasks.fail_stale_imports")
def fail_stale_imports():
    return importer.fail_stale_imports()
