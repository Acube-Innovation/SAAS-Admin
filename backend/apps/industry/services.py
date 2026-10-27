"""Workflow 3.5: template changes are versioned, audited and safe for clients using them."""

from django.db import transaction
from django.db.models import Count, Prefetch

from apps.audit.models import Action
from apps.audit.services import write_audit
from apps.core.exceptions import ApiError
from apps.core.models import Tenant

from .models import FieldDefinition, IndustryTemplate, IndustryTemplateVersion, ScheduleType


def templates_queryset():
    fields = FieldDefinition.objects.order_by("order", "key")
    return IndustryTemplate.objects.annotate(clients_using=Count("tenants")).prefetch_related(
        Prefetch("fields", queryset=fields.filter(entity="subscriber")),
        Prefetch(
            "schedule_types",
            queryset=ScheduleType.objects.prefetch_related(Prefetch("fields", queryset=fields)),
        ),
    )


def _field_dict(field):
    return {
        "key": field.key,
        "label": field.label,
        "field_type": field.field_type,
        "required": field.required,
        "is_core": field.is_core,
        "options": field.options,
        "max_length": field.max_length,
        "is_active": field.is_active,
    }


def definition_of(template: IndustryTemplate) -> dict:
    """The full current definition (inactive fields included), as stored in each version."""
    fields = list(FieldDefinition.objects.filter(template=template).order_by("order", "key"))
    return {
        "key": template.key,
        "name": template.name,
        "description": template.description,
        "is_active": template.is_active,
        "subscriber_fields": [_field_dict(f) for f in fields if f.entity == "subscriber"],
        "schedule_types": [
            {
                "key": st.key,
                "name": st.name,
                "is_active": st.is_active,
                "fields": [_field_dict(f) for f in fields if f.schedule_type_id == st.pk],
                "default_reminder_rule": st.default_reminder_rule,
                "default_question_flow": st.default_question_flow,
            }
            for st in template.schedule_types.order_by("order", "key")
        ],
    }


def is_in_use(template: IndustryTemplate) -> bool:
    return Tenant.objects.filter(industry_template=template).exists()


def _conflict(code, detail):
    return ApiError(code, detail, 409)


def _sync_fields(template, schedule_type, entity, incoming, existing, in_use):
    """Apply the 3.5 field rules for one field list (subscriber fields or one schedule type)."""
    existing_by_key = {f.key: f for f in existing}
    seen = set()
    for order, data in enumerate(incoming):
        key = data["key"]
        seen.add(key)
        field = existing_by_key.get(key)
        if (
            (field is None or not field.is_core)
            and data["field_type"] == "choice"
            and not data.get("options")
        ):
            raise ApiError("validation_error", f"Choice field '{key}' needs options.", 400)
        if field is None:
            if data["required"] and data["is_active"] and in_use:
                raise _conflict(
                    "required_field_in_use",
                    f"New required field '{key}' can't be added: clients already use this "
                    "template. Add it as optional.",
                )
            FieldDefinition.objects.create(
                template=template,
                schedule_type=schedule_type,
                entity=entity,
                order=order,
                is_core=False,
                key=key,
                label=data["label"],
                field_type=data["field_type"],
                required=data["required"],
                options=data.get("options") or [],
                max_length=data.get("max_length"),
                is_active=data["is_active"],
            )
            continue

        if field.is_core:
            # Core fields map to model columns: only the label (and order) can change.
            if any(data[a] != getattr(field, a) for a in ("field_type", "required", "is_active")):
                raise ApiError(
                    "core_field_locked",
                    f"'{key}' is a core field: only its label can change.",
                    400,
                )
            field.label, field.order = data["label"], order
            field.save(update_fields=["label", "order", "updated_at"])
            continue

        if in_use and data["field_type"] != field.field_type:
            raise _conflict(
                "field_type_locked",
                f"The type of '{key}' can't change: clients already use this template.",
            )
        becomes_required = (
            data["required"] and data["is_active"] and not (field.required and field.is_active)
        )
        if in_use and becomes_required:
            raise _conflict(
                "required_field_in_use",
                f"'{key}' can't become required: clients already use this template.",
            )
        field.label = data["label"]
        field.field_type = data["field_type"]
        field.required = data["required"]
        field.options = data.get("options") or []
        field.max_length = data.get("max_length")
        field.is_active = data["is_active"]
        field.order = order
        field.save()

    for key, field in existing_by_key.items():
        if key in seen or not field.is_active:
            continue
        if field.is_core:
            raise ApiError(
                "core_field_locked", f"'{key}' is a core field and can't be removed.", 400
            )
        # Removed fields are switched off and kept, so existing data stays readable.
        field.is_active = False
        field.save(update_fields=["is_active", "updated_at"])


def _apply(template, data, in_use):
    template.name = data["name"]
    template.description = data.get("description", "")
    template.is_active = data["is_active"]

    existing_subscriber = list(template.fields.filter(entity="subscriber"))
    _sync_fields(
        template, None, "subscriber", data["subscriber_fields"], existing_subscriber, in_use
    )

    existing_types = {st.key: st for st in template.schedule_types.all()}
    seen = set()
    for order, st_data in enumerate(data["schedule_types"]):
        seen.add(st_data["key"])
        schedule_type = existing_types.get(st_data["key"])
        if schedule_type is None:
            schedule_type = ScheduleType.objects.create(template=template, key=st_data["key"])
        schedule_type.name = st_data["name"]
        schedule_type.is_active = st_data["is_active"]
        schedule_type.order = order
        # Default rule and flow: copied onto each tenant when it is created, so changes here
        # reach new clients only.
        schedule_type.default_reminder_rule = st_data["default_reminder_rule"]
        schedule_type.default_question_flow = st_data["default_question_flow"]
        schedule_type.save()
        _sync_fields(
            template,
            schedule_type,
            "schedule",
            st_data["fields"],
            list(schedule_type.fields.all()),
            in_use,
        )
    for key, schedule_type in existing_types.items():
        if key not in seen and schedule_type.is_active:
            schedule_type.is_active = False
            schedule_type.save(update_fields=["is_active", "updated_at"])


@transaction.atomic
def create_template(data: dict, user) -> IndustryTemplate:
    if IndustryTemplate.objects.filter(key=data["key"]).exists():
        raise ApiError("key_taken", "A template with this key already exists.", 409)
    template = IndustryTemplate.objects.create(
        key=data["key"], name=data["name"], current_version=1
    )
    _apply(template, data, in_use=False)
    template.save()
    definition = definition_of(template)
    IndustryTemplateVersion.objects.create(
        template=template, version=1, definition=definition, created_by=user
    )
    write_audit(Action.CREATE, template, after=definition, tenant=None)
    return template


@transaction.atomic
def save_template(template: IndustryTemplate, data: dict, user) -> IndustryTemplate:
    """Save the editor's full definition as a new version (Workflow 3.5)."""
    template = IndustryTemplate.objects.select_for_update().get(pk=template.pk)
    if data["key"] != template.key:
        raise ApiError("key_immutable", "A template's key can't change.", 400)
    before = definition_of(template)
    _apply(template, data, in_use=is_in_use(template))
    template.current_version += 1
    template.save()
    after = definition_of(template)
    IndustryTemplateVersion.objects.create(
        template=template, version=template.current_version, definition=after, created_by=user
    )
    write_audit(
        Action.UPDATE,
        template,
        before={**before, "version": template.current_version - 1},
        after={**after, "version": template.current_version},
        tenant=None,
    )
    return template


# --- tenants -----------------------------------------------------------------------------------


def defaults_for_new_tenant(template: IndustryTemplate) -> dict:
    """The defaults a new client gets a copy of (Workflow 3.1 step 2)."""
    schedule_types = {}
    choice_options = {}
    for st in template.schedule_types.filter(is_active=True):
        schedule_types[st.key] = {
            "reminder_rule": st.default_reminder_rule,
            "question_flow": st.default_question_flow,
        }
    for field in template.fields.filter(is_active=True, field_type="choice", is_core=False):
        scope = field.schedule_type.key if field.schedule_type_id else "subscriber"
        choice_options[f"{scope}.{field.key}"] = list(field.options)
    return {"schedule_types": schedule_types, "choice_options": choice_options}


def assign_template(tenant: Tenant, template: IndustryTemplate) -> None:
    tenant.industry_template = template
    tenant.industry_template_version = template.current_version
    tenant.settings = {**(tenant.settings or {}), "industry": defaults_for_new_tenant(template)}


def tenant_config(tenant: Tenant) -> dict:
    """
    What a client works with: the template's active fields (live, so new optional fields apply to
    every client) and the client's own copy of the defaults (rule, flow, choice lists).
    """
    template = tenant.industry_template
    copied = (tenant.settings or {}).get("industry", {})
    copied_types = copied.get("schedule_types", {})
    copied_choices = copied.get("choice_options", {})

    def fields_for(scope, fields):
        result = []
        for field in fields:
            item = _field_dict(field)
            if field.field_type == "choice" and not field.is_core:
                item["options"] = copied_choices.get(f"{scope}.{field.key}", field.options)
            result.append(item)
        return result

    active = FieldDefinition.objects.filter(template=template, is_active=True).order_by(
        "order", "key"
    )
    schedule_types = []
    for st in template.schedule_types.filter(is_active=True):
        own = copied_types.get(st.key, {})
        schedule_types.append(
            {
                "key": st.key,
                "name": st.name,
                "fields": fields_for(st.key, [f for f in active if f.schedule_type_id == st.pk]),
                "reminder_rule": own.get("reminder_rule", st.default_reminder_rule),
                "question_flow": own.get("question_flow", st.default_question_flow),
            }
        )
    return {
        "template": template.key,
        "subscriber_fields": fields_for(
            "subscriber", [f for f in active if f.entity == "subscriber"]
        ),
        "schedule_types": schedule_types,
    }
