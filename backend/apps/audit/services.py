import json

from django.core.serializers.json import DjangoJSONEncoder

from apps.core.tenancy import db_context, get_current_tenant_id

from .context import get_request_meta
from .models import AuditLog

# Never written to the audit log in clear.
SENSITIVE_FIELDS = {"password", "token_hash", "key", "secret", "api_key", "key_hash"}
MASK = "***"

_UNSET = object()


def _json_safe(data):
    if data is None:
        return None
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def snapshot(instance, fields=None, exclude=()):
    """A JSON-safe dict of a model instance's concrete field values (FKs as ids), secrets masked."""
    if instance is None:
        return None
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in exclude or (fields is not None and field.name not in fields):
            continue
        key = field.attname if field.is_relation else field.name
        value = field.value_from_object(instance)
        data[key] = MASK if field.name in SENSITIVE_FIELDS and value else value
    return _json_safe(data)


def _tenant_of(obj):
    if obj is None:
        return None
    from apps.core.models import Tenant

    if isinstance(obj, Tenant):
        return obj.pk
    return getattr(obj, "tenant_id", None)


def write_audit(
    action,
    obj=None,
    *,
    before=None,
    after=None,
    tenant=_UNSET,
    actor=_UNSET,
    object_type=None,
    object_id=None,
    support_session=_UNSET,
):
    """
    Record one audit entry. Actor, IP, user agent and support session come from the current request
    (AuditContextMiddleware) unless given. The tenant is, in order: `tenant`, the object's tenant,
    the current tenant context. The insert runs with app.tenant_id set to that tenant, as the
    audit log's RLS write rule requires.
    """
    meta = get_request_meta()
    if tenant is _UNSET:
        tenant = _tenant_of(obj) or get_current_tenant_id()
    tenant_id = getattr(tenant, "pk", tenant)

    if actor is _UNSET:
        actor_id, actor_email = meta.actor_id, meta.actor_email
    else:
        actor_id = getattr(actor, "pk", None)
        actor_email = getattr(actor, "email", "") or ""

    if obj is not None:
        object_type = object_type or obj._meta.label_lower
        object_id = object_id or str(obj.pk)

    with db_context(tenant=tenant_id):
        return AuditLog.objects.create(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_email=actor_email,
            support_session_id=(
                meta.support_session_id
                if support_session is _UNSET
                else getattr(support_session, "pk", support_session)
            ),
            action=action,
            object_type=object_type or "",
            object_id=str(object_id or ""),
            before=_json_safe(before),
            after=_json_safe(after),
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
        )
