import logging

from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


def grant_app_role_privileges(using="default", **kwargs):
    """
    After migrations, give the app role DML on every table and sequence (it never owns them).
    It may read django_migrations but not change it. Runs as the migrating (owner) role.
    """
    from django.db import connections

    app_role = settings.DATABASE_ROLES["app"][0]
    connection = connections[using]
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [app_role])
        if cursor.fetchone() is None:
            logger.warning("Database role %s does not exist; skipping grants", app_role)
            return
        role = connection.ops.quote_name(app_role)
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        cursor.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"
        )
        cursor.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
        cursor.execute(f"REVOKE INSERT, UPDATE, DELETE ON django_migrations FROM {role}")


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    def ready(self):
        post_migrate.connect(grant_app_role_privileges, sender=self)
