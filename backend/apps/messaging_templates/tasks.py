from celery import shared_task

from .services import sync_all_statuses


@shared_task(name="apps.messaging_templates.tasks.sync_template_statuses")
def sync_template_statuses():
    return sync_all_statuses()
