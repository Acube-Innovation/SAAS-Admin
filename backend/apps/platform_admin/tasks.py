from celery import shared_task

from .services import expire_support_sessions


@shared_task(name="apps.platform_admin.tasks.expire_support_sessions")
def expire_support_sessions_task():
    return expire_support_sessions()
