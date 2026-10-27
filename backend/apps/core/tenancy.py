"""
Tenant context: which tenant (and user) the current request or task acts for.

The database enforces isolation. Every tenant table has an RLS policy comparing tenant_id with the
transaction-local setting app.tenant_id, which db_context() sets with set_config(..., true).
Without a tenant context, tenant tables return no rows and reject writes. app.user_id lets a
signed-in user read their own rows across tenants where a policy allows it (memberships).
"""

import contextvars
import uuid
from contextlib import contextmanager

from celery import Task
from django.db import connection, transaction

TENANT_SETTING = "app.tenant_id"
USER_SETTING = "app.user_id"

_current = {
    TENANT_SETTING: contextvars.ContextVar("current_tenant_id", default=None),
    USER_SETTING: contextvars.ContextVar("current_user_id", default=None),
}
_UNSET = object()


def get_current_tenant_id() -> uuid.UUID | None:
    value = _current[TENANT_SETTING].get()
    return uuid.UUID(value) if value else None


def get_current_user_id() -> uuid.UUID | None:
    value = _current[USER_SETTING].get()
    return uuid.UUID(value) if value else None


def _normalise(obj) -> str | None:
    if obj is None:
        return None
    value = getattr(obj, "pk", obj)
    return str(uuid.UUID(str(value)))


def _set_db(name: str, value: str | None):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [name, value or ""])


@contextmanager
def db_context(tenant=_UNSET, user=_UNSET):
    """
    Run the block inside a transaction with app.tenant_id and/or app.user_id set. Each accepts a
    model instance, UUID, string or None ("none"); an argument left out keeps its current value.
    Nested contexts restore the outer values on exit.
    """
    changes = {}
    if tenant is not _UNSET:
        changes[TENANT_SETTING] = _normalise(tenant)
    if user is not _UNSET:
        changes[USER_SETTING] = _normalise(user)
    previous = {name: _current[name].get() for name in changes}

    with transaction.atomic():
        tokens = {}
        for name, value in changes.items():
            _set_db(name, value)
            tokens[name] = _current[name].set(value)
        try:
            yield
        except BaseException:
            # The atomic block rolls back (savepoint or transaction), reverting the settings too.
            for name, token in tokens.items():
                _current[name].reset(token)
            raise
        else:
            for name, token in tokens.items():
                _current[name].reset(token)
            # A database error caught inside the block leaves the transaction marked for rollback;
            # that rollback reverts the settings, and no query may run before it.
            if not connection.needs_rollback:
                for name, value in previous.items():
                    _set_db(name, value)


def tenant_context(tenant):
    """Run the block with app.tenant_id set to `tenant` (None for "no tenant")."""
    return db_context(tenant=tenant)


def user_context(user):
    """Run the block with app.user_id set to `user`, keeping the current tenant."""
    return db_context(user=user)


def resolve_no_tenant(request):
    """Resolver that never picks a tenant (useful in tests)."""
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
