import logging

from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


def revoke_audit_changes(using="default", **kwargs):
    """
    The app role may insert and read audit entries but never change or delete them. Runs after
    apps.core's grant handler (core comes first in INSTALLED_APPS), which grants DML on all tables.
    """
    from django.db import connections

    from .models import AuditLog

    app_role = settings.DATABASE_ROLES["app"][0]
    connection = connections[using]
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [app_role])
        if cursor.fetchone() is None:
            return
        table = connection.ops.quote_name(AuditLog._meta.db_table)
        cursor.execute(
            f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {connection.ops.quote_name(app_role)}"
        )


class AuditConfig(AppConfig):
    name = "apps.audit"
    label = "audit"

    def ready(self):
        post_migrate.connect(revoke_audit_changes, sender=self)
