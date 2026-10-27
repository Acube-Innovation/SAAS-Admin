"""
Tenant context: which tenant the current request or task acts for.

The database enforces isolation. Every tenant table has an RLS policy comparing tenant_id with the
transaction-local setting app.tenant_id, which tenant_context() sets with set_config(..., true).
Without a tenant context, tenant tables return no rows and reject writes.
"""

import contextvars
import uuid
from contextlib import contextmanager

from celery import Task
from django.db import connection, transaction

TENANT_SETTING = "app.tenant_id"

_current_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tenant_id", default=None
)


def get_current_tenant_id() -> uuid.UUID | None:
    value = _current_tenant_id.get()
    return uuid.UUID(value) if value else None


def _normalise(tenant) -> str | None:
    if tenant is None:
        return None
    value = getattr(tenant, "pk", tenant)
    return str(uuid.UUID(str(value)))


def _set_db_tenant(value: str | None):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [TENANT_SETTING, value or ""])


@contextmanager
def tenant_context(tenant):
    """
    Run the block inside a transaction with app.tenant_id set to `tenant` (a Tenant, UUID, string
    or None for "no tenant"). Nested contexts restore the outer tenant on exit.
    """
    value = _normalise(tenant)
    previous = _current_tenant_id.get()
    with transaction.atomic():
        _set_db_tenant(value)
        token = _current_tenant_id.set(value)
        try:
            yield
        except BaseException:
            # The atomic block rolls back (savepoint or transaction), which reverts the setting too.
            _current_tenant_id.reset(token)
            raise
        else:
            _current_tenant_id.reset(token)
            # A database error caught inside the block leaves the transaction marked for rollback;
            # that rollback reverts the setting, and no query may run before it.
            if not connection.needs_rollback:
                _set_db_tenant(previous)


def resolve_no_tenant(request):
    """Default resolver until accounts exist (Step 3): requests act without a tenant."""
    return None


class TenantTask(Task):
    """
    Celery base task that runs inside tenant_context(tenant_id).

    Tasks using it take a `tenant_id` keyword argument. When queued from inside a tenant context,
    apply_async() fills it in automatically, so `my_task.delay(...)` carries the caller's tenant.
    """

    abstract = True

    def apply_async(self, args=None, kwargs=None, **options):
        kwargs = dict(kwargs or {})
        if "tenant_id" not in kwargs:
            current = get_current_tenant_id()
            if current is None:
                raise ValueError(f"{self.name} needs a tenant_id keyword argument")
            kwargs["tenant_id"] = str(current)
        return super().apply_async(args=args, kwargs=kwargs, **options)

    def __call__(self, *args, **kwargs):
        if "tenant_id" not in kwargs:
            raise ValueError(f"{self.name} needs a tenant_id keyword argument")
        with tenant_context(kwargs["tenant_id"]):
            return super().__call__(*args, **kwargs)
