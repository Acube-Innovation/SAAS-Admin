"""
Cross-tenant isolation through PostgreSQL Row-Level Security.

These tests connect as the table owner (app_owner), which FORCE ROW LEVEL SECURITY also restricts.
tests/test_rls_app_role.py repeats the key checks connected as the runtime app role.
"""

import pytest
from django.apps import apps
from django.db import ProgrammingError, connection
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from apps.core.middleware import TenantMiddleware
from apps.core.models import Tenant, TenantScopedModel
from apps.core.tenancy import get_current_tenant_id, tenant_context
from tests.tenancy_app.models import Note
from tests.tenancy_app.tasks import count_notes

pytestmark = pytest.mark.django_db

NOTE_TABLE = Note._meta.db_table


def make_tenant(name):
    return Tenant.objects.create(
        name=name,
        legal_name=f"{name} Pvt Ltd",
        address_line1="1 Main Road",
        city="Kochi",
        state="KL",
        pin_code="682001",
        contact_name="Contact",
        contact_phone="+919876543210",
        contact_email=f"{name.lower().replace(' ', '')}@example.com",
    )


@pytest.fixture
def tenant_a():
    return make_tenant("Agency A")


@pytest.fixture
def tenant_b():
    return make_tenant("Agency B")


@pytest.fixture
def notes(tenant_a, tenant_b):
    with tenant_context(tenant_a):
        a_notes = [Note.objects.create(body=f"a{i}") for i in range(3)]
    with tenant_context(tenant_b):
        b_notes = [Note.objects.create(body=f"b{i}") for i in range(2)]
    return {"a": a_notes, "b": b_notes}


def raw(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return cursor.fetchall()


# --- reads -------------------------------------------------------------------------------------


def test_orm_query_under_tenant_a_returns_only_a_rows(tenant_a, notes):
    with tenant_context(tenant_a):
        assert set(Note.objects.values_list("body", flat=True)) == {"a0", "a1", "a2"}
        assert set(Note.objects.values_list("tenant_id", flat=True)) == {tenant_a.id}


def test_orm_get_of_other_tenants_row_is_not_found(tenant_a, notes):
    with tenant_context(tenant_a):
        assert not Note.objects.filter(pk=notes["b"][0].pk).exists()


def test_raw_sql_under_tenant_a_returns_only_a_rows(tenant_a, notes):
    with tenant_context(tenant_a):
        rows = raw(f"SELECT tenant_id FROM {NOTE_TABLE}")
    assert len(rows) == 3
    assert {row[0] for row in rows} == {tenant_a.id}


def test_raw_sql_filtering_on_tenant_b_returns_nothing_under_tenant_a(tenant_a, tenant_b, notes):
    with tenant_context(tenant_a):
        assert raw(f"SELECT * FROM {NOTE_TABLE} WHERE tenant_id = %s", [tenant_b.id]) == []
        assert raw(f"SELECT count(*) FROM {NOTE_TABLE} WHERE true OR tenant_id IS NULL") == [(3,)]


def test_without_tenant_context_tenant_tables_are_empty(notes):
    assert Note.objects.count() == 0
    assert raw(f"SELECT count(*) FROM {NOTE_TABLE}") == [(0,)]
    with tenant_context(None):
        assert Note.objects.count() == 0


# --- writes ------------------------------------------------------------------------------------


def test_insert_for_another_tenant_is_rejected(tenant_a, tenant_b):
    with tenant_context(tenant_a), pytest.raises(ProgrammingError, match="row-level security"):
        Note.objects.create(body="sneaky", tenant=tenant_b)


def test_insert_without_tenant_context_is_rejected(tenant_a):
    with pytest.raises(ProgrammingError, match="row-level security"), tenant_context(None):
        Note.objects.create(body="orphan", tenant=tenant_a)


def test_update_and_delete_cannot_touch_other_tenants_rows(tenant_a, notes):
    b_note = notes["b"][0]
    with tenant_context(tenant_a):
        assert Note.objects.filter(pk=b_note.pk).update(body="changed") == 0
        assert Note.objects.filter(pk=b_note.pk).delete()[0] == 0
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE {NOTE_TABLE} SET body = 'x' WHERE id = %s", [b_note.pk])
            assert cursor.rowcount == 0
            cursor.execute(f"DELETE FROM {NOTE_TABLE} WHERE id = %s", [b_note.pk])
            assert cursor.rowcount == 0
    with tenant_context(b_note.tenant_id):
        assert Note.objects.get(pk=b_note.pk).body == "b0"


def test_moving_a_row_to_another_tenant_is_rejected(tenant_a, tenant_b, notes):
    with tenant_context(tenant_a), pytest.raises(ProgrammingError, match="row-level security"):
        Note.objects.filter(pk=notes["a"][0].pk).update(tenant=tenant_b)


def test_save_fills_in_the_current_tenant(tenant_a):
    with tenant_context(tenant_a):
        note = Note.objects.create(body="auto")
    assert note.tenant_id == tenant_a.id


# --- context handling --------------------------------------------------------------------------


def test_tenant_setting_is_cleared_when_the_context_ends(tenant_a):
    with tenant_context(tenant_a):
        assert raw("SELECT current_setting('app.tenant_id', true)") == [(str(tenant_a.id),)]
        assert get_current_tenant_id() == tenant_a.id
    assert raw("SELECT current_setting('app.tenant_id', true)") == [("",)]
    assert get_current_tenant_id() is None


def test_nested_contexts_restore_the_outer_tenant(tenant_a, tenant_b, notes):
    with tenant_context(tenant_a):
        with tenant_context(tenant_b):
            assert Note.objects.count() == 2
        assert Note.objects.count() == 3
        assert get_current_tenant_id() == tenant_a.id


def test_failed_block_does_not_leak_its_tenant(tenant_a, notes):
    with pytest.raises(RuntimeError), tenant_context(tenant_a):
        raise RuntimeError("boom")
    assert get_current_tenant_id() is None
    assert Note.objects.count() == 0


def test_every_tenant_scoped_model_has_rls_enabled_forced_and_a_policy():
    models = [
        model
        for model in apps.get_models()
        if issubclass(model, TenantScopedModel) and not model._meta.proxy
    ]
    assert models, "expected at least one tenant-scoped model"
    for model in models:
        table = model._meta.db_table
        [(enabled, forced)] = raw(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            [table],
        )
        policies = raw("SELECT policyname FROM pg_policies WHERE tablename = %s", [table])
        assert enabled and forced, f"{table}: add EnableRLS to its migration"
        assert ("tenant_isolation",) in policies, f"{table}: missing tenant_isolation policy"


# --- middleware --------------------------------------------------------------------------------


def resolve_from_test_header(request):
    """Test-only resolver: tenant id from a header."""
    return request.headers.get("X-Test-Tenant")


def count_notes_view(request):
    return HttpResponse(str(Note.objects.count()))


@override_settings(TENANCY_RESOLVER="tests.test_tenancy.resolve_from_test_header")
def test_middleware_runs_the_request_under_the_resolved_tenant(tenant_a, tenant_b, notes):
    middleware = TenantMiddleware(count_notes_view)
    factory = RequestFactory()

    request = factory.get("/", HTTP_X_TEST_TENANT=str(tenant_a.id))
    assert middleware(request).content == b"3"
    assert str(request.tenant_id) == str(tenant_a.id)

    assert middleware(factory.get("/", HTTP_X_TEST_TENANT=str(tenant_b.id))).content == b"2"
    assert middleware(factory.get("/")).content == b"0"


def test_default_resolver_gives_no_tenant(notes):
    request = RequestFactory().get("/")
    assert TenantMiddleware(count_notes_view)(request).content == b"0"
    assert request.tenant_id is None


# --- celery ------------------------------------------------------------------------------------


def test_tenant_task_runs_under_the_given_tenant(tenant_a, tenant_b, notes):
    assert count_notes.delay(tenant_id=str(tenant_a.id)).get() == 3
    assert count_notes.delay(tenant_id=str(tenant_b.id)).get() == 2


def test_tenant_task_inherits_the_callers_tenant(tenant_b, notes):
    with tenant_context(tenant_b):
        assert count_notes.delay().get() == 2


def test_tenant_task_without_a_tenant_is_refused(notes):
    with pytest.raises(ValueError, match="tenant_id"):
        count_notes.delay()
    with pytest.raises(ValueError, match="tenant_id"):
        count_notes()
