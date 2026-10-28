"""
Create the application database roles on a managed database (Amazon RDS), where the Docker init
script (infra/docker/postgres/init-roles.sh) cannot run. Same result, idempotent; passwords are
updated to the current secrets on every run.

Connects as the database's admin user: POSTGRES_ADMIN_USER / POSTGRES_ADMIN_PASSWORD.
"""

import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from psycopg import sql

from config.settings.base import env

ROLE_OPTIONS = {
    "owner": "LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOBYPASSRLS",
    "app": "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS",
}


def statements(db_name, owner, owner_password, app, app_password, existing):
    """The SQL to run, given the set of role names that already exist."""
    out = []
    for key, name, password in (("owner", owner, owner_password), ("app", app, app_password)):
        # Only a superuser may restate SUPERUSER/BYPASSRLS, so existing roles just get the current
        # password (the command checks their attributes separately).
        options = "LOGIN" if name in existing else ROLE_OPTIONS[key]
        verb = "ALTER" if name in existing else "CREATE"
        out.append(
            sql.SQL(f"{verb} ROLE {{}} {options} PASSWORD {{}}").format(
                sql.Identifier(name), sql.Literal(password)
            )
        )
    # The admin user needs membership to hand the database over (PostgreSQL 16 rules).
    out.append(sql.SQL("GRANT {} TO CURRENT_USER").format(sql.Identifier(owner)))
    # The database owner owns the public schema (pg_database_owner): migrations can create tables.
    out.append(
        sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
            sql.Identifier(db_name), sql.Identifier(owner)
        )
    )
    out.append(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(db_name), sql.Identifier(app)
        )
    )
    return out


class Command(BaseCommand):
    help = "Create or update the app_owner and app_user database roles (managed databases)."

    def handle(self, *args, **options):
        admin_user = env("POSTGRES_ADMIN_USER")
        admin_password = env("POSTGRES_ADMIN_PASSWORD")
        if not admin_user or not admin_password:
            raise CommandError("Set POSTGRES_ADMIN_USER and POSTGRES_ADMIN_PASSWORD.")
        db = settings.DATABASES["default"]
        (owner, owner_password), (app, app_password) = (
            settings.DATABASE_ROLES["owner"],
            settings.DATABASE_ROLES["app"],
        )
        if not owner_password or not app_password:
            raise CommandError("Set POSTGRES_OWNER_PASSWORD and POSTGRES_APP_PASSWORD.")

        with psycopg.connect(
            host=db["HOST"],
            port=db["PORT"],
            dbname=db["NAME"],
            user=admin_user,
            password=admin_password,
            sslmode=db.get("OPTIONS", {}).get("sslmode", "prefer"),
            autocommit=True,
        ) as connection:
            rows = connection.execute(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = ANY(%s)",
                [[owner, app]],
            ).fetchall()
            unsafe = [name for name, superuser, bypassrls in rows if superuser or bypassrls]
            if unsafe:
                raise CommandError(
                    f"Role(s) {', '.join(unsafe)} can bypass Row-Level Security (SUPERUSER or "
                    "BYPASSRLS). Fix them by hand; the app must not connect with such a role."
                )
            existing = {name for name, _, _ in rows}
            for statement in statements(
                db["NAME"], owner, owner_password, app, app_password, existing
            ):
                connection.execute(statement)
        self.stdout.write(self.style.SUCCESS(f"Database roles {owner} and {app} are ready."))
