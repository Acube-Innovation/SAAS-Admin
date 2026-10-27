"""
Connects to the test database as the runtime app role (app_user) and proves RLS is enforced for it,
and that it cannot switch RLS off, drop the policy or become another role.
"""

import psycopg
import pytest
from django.conf import settings
from django.db import connection
from psycopg import errors, sql

from apps.core.models import Tenant
from apps.core.tenancy import tenant_context
from tests.tenancy_app.models import Note

# transaction=True: rows must be committed so the separate app-role connection can see them.
pytestmark = pytest.mark.django_db(transaction=True)

NOTE_TABLE = Note._meta.db_table


def make_tenant(name):
    return Tenant.objects.create(
        name=name,
        legal_name=name,
        address_line1="1 Main Road",
        city="Kochi",
        state="KL",
        pin_code="682001",
        contact_name="Contact",
        contact_phone="+919876543210",
        contact_email="contact@example.com",
    )


@pytest.fixture
def tenants():
    a, b = make_tenant("Agency A"), make_tenant("Agency B")
    with tenant_context(a):
        Note.objects.create(body="a0")
        Note.objects.create(body="a1")
    with tenant_context(b):
        Note.objects.create(body="b0")
    return a, b


@pytest.fixture
def app_conn():
    user, password = settings.DATABASE_ROLES["app"]
    db = connection.settings_dict
    conn = psycopg.connect(
        dbname=db["NAME"],
        user=user,
        password=password,
        host=db["HOST"],
        port=db["PORT"],
        autocommit=True,
    )
    yield conn
    conn.close()


def query(conn, sql, params=None, tenant=None):
    with conn.transaction(), conn.cursor() as cursor:
        if tenant is not None:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant)])
        cursor.execute(sql, params)
        return cursor.fetchall()


def test_app_and_owner_roles_cannot_bypass_rls(app_conn):
    [(current_user, is_super, bypass)] = query(
        app_conn,
        "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user",
    )
    assert current_user == settings.DATABASE_ROLES["app"][0]
    assert (is_super, bypass) == (False, False)

    owner = settings.DATABASE_ROLES["owner"][0]
    [(owner_super, owner_bypass)] = query(
        app_conn, "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", [owner]
    )
    assert (owner_super, owner_bypass) == (False, False)


def test_app_role_does_not_own_tenant_tables(app_conn):
    [(owner,)] = query(
        app_conn, "SELECT tableowner FROM pg_tables WHERE tablename = %s", [NOTE_TABLE]
    )
    assert owner == settings.DATABASE_ROLES["owner"][0]


def test_app_role_sees_no_rows_without_a_tenant(app_conn, tenants):
    assert query(app_conn, f"SELECT count(*) FROM {NOTE_TABLE}") == [(0,)]


def test_app_role_sees_only_the_current_tenants_rows(app_conn, tenants):
    a, b = tenants
    rows = query(app_conn, f"SELECT tenant_id, body FROM {NOTE_TABLE} ORDER BY body", tenant=a.id)
    assert rows == [(a.id, "a0"), (a.id, "a1")]
    assert query(
        app_conn, f"SELECT count(*) FROM {NOTE_TABLE} WHERE tenant_id = %s", [b.id], tenant=a.id
    ) == [(0,)]


def test_app_role_cannot_insert_rows_for_another_tenant(app_conn, tenants):
    a, b = tenants
    with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
        query(
            app_conn,
            f"INSERT INTO {NOTE_TABLE} (id, tenant_id, body, created_at, updated_at) "
            "VALUES (gen_random_uuid(), %s, 'x', now(), now()) RETURNING id",
            [b.id],
            tenant=a.id,
        )


def test_app_role_cannot_turn_row_security_off(app_conn, tenants):
    with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
        query(app_conn, f"SET LOCAL row_security = off; SELECT count(*) FROM {NOTE_TABLE}")


@pytest.mark.parametrize(
    "statement",
    [
        f"ALTER TABLE {NOTE_TABLE} DISABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {NOTE_TABLE} NO FORCE ROW LEVEL SECURITY",
        f"DROP POLICY tenant_isolation ON {NOTE_TABLE}",
        f"CREATE POLICY open_all ON {NOTE_TABLE} USING (true)",
    ],
)
def test_app_role_cannot_change_rls(app_conn, statement):
    with pytest.raises(errors.InsufficientPrivilege), app_conn.cursor() as cursor:
        cursor.execute(statement)


def test_app_role_cannot_become_the_owner_role(app_conn):
    owner = settings.DATABASE_ROLES["owner"][0]
    with pytest.raises(errors.InsufficientPrivilege), app_conn.cursor() as cursor:
        cursor.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner)))


def test_app_role_cannot_rewrite_migration_history(app_conn):
    assert query(app_conn, "SELECT count(*) > 0 FROM django_migrations") == [(True,)]
    with pytest.raises(errors.InsufficientPrivilege), app_conn.cursor() as cursor:
        cursor.execute("DELETE FROM django_migrations")


def test_app_role_can_insert_but_never_change_audit_entries(app_conn, tenants):
    a, _b = tenants
    [(entry_id,)] = query(
        app_conn,
        "INSERT INTO audit_auditlog (id, created_at, tenant_id, action, actor_email, object_type,"
        " object_id, user_agent) VALUES (gen_random_uuid(), now(), %s, 'probe', '', '', '', '')"
        " RETURNING id",
        [a.id],
        tenant=a.id,
    )
    assert query(
        app_conn, "SELECT action FROM audit_auditlog WHERE id = %s", [entry_id], tenant=a.id
    ) == [("probe",)]
    for statement in (
        "UPDATE audit_auditlog SET action = 'tampered'",
        "DELETE FROM audit_auditlog",
        "TRUNCATE audit_auditlog",
    ):
        with pytest.raises(errors.InsufficientPrivilege), app_conn.cursor() as cursor:
            cursor.execute(statement)
