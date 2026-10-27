from .models import Action
from .services import snapshot, write_audit


class AuditedModelMixin:
    """
    For DRF generic views / viewsets: every create, update and delete writes an audit entry with
    before/after JSON. An update that changes one of `audit_status_fields` is recorded as a
    status_change. Custom actions can call self.audit(...) directly.
    """

    audit_status_fields = ("status",)
    audit_exclude_fields = ()

    def audit_snapshot(self, instance):
        return snapshot(instance, exclude=self.audit_exclude_fields)

    def audit(self, action, instance, before=None, after=None):
        return write_audit(action, instance, before=before, after=after)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self.audit(
            Action.CREATE, serializer.instance, after=self.audit_snapshot(serializer.instance)
        )

    def perform_update(self, serializer):
        before = self.audit_snapshot(serializer.instance)
        super().perform_update(serializer)
        after = self.audit_snapshot(serializer.instance)
        status_changed = any(
            field in before and before[field] != after.get(field)
            for field in self.audit_status_fields
        )
        action = Action.STATUS_CHANGE if status_changed else Action.UPDATE
        self.audit(action, serializer.instance, before=before, after=after)

    def perform_destroy(self, instance):
        before = self.audit_snapshot(instance)
        tenant_id, object_id = getattr(instance, "tenant_id", None), str(instance.pk)
        super().perform_destroy(instance)
        write_audit(
            Action.DELETE,
            before=before,
            tenant=tenant_id,
            object_type=instance._meta.label_lower,
            object_id=object_id,
        )
