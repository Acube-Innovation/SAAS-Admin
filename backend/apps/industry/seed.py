"""
The gas_agency template (spec 2.6 seed, from the Functional Spec). seed_template() takes the model
classes so the data migration can pass its historical models.
"""

DELIVERY_WEEKDAYS_MON_SAT = [0, 1, 2, 3, 4, 5]

GAS_AGENCY = {
    "key": "gas_agency",
    "name": "Gas agency",
    "description": "LPG distributors: cylinder delivery reminders over WhatsApp.",
    "subscriber_fields": [
        {
            "key": "customer_name",
            "label": "Customer name",
            "field_type": "text",
            "required": True,
            "is_core": True,
            "max_length": 100,
        },
        {
            "key": "mobile_number",
            "label": "Mobile number",
            "field_type": "phone",
            "required": True,
            "is_core": True,
        },
        {
            "key": "alternate_number",
            "label": "Alternate number",
            "field_type": "phone",
            "required": False,
            "is_core": True,
        },
        {
            "key": "external_ref",
            "label": "Consumer number",
            "field_type": "text",
            "required": True,
            "is_core": True,
            "max_length": 50,
        },
        {
            "key": "preferred_language",
            "label": "Preferred language",
            "field_type": "choice",
            "required": True,
            "is_core": True,
            "options": ["ml", "en", "hi", "ta"],
        },
        {
            "key": "address",
            "label": "Address",
            "field_type": "text",
            "required": False,
            "is_core": True,
            "max_length": 500,
        },
        {
            "key": "area",
            "label": "Area / route",
            "field_type": "choice",
            "required": False,
            "is_core": True,
        },
        {
            "key": "whatsapp_consent",
            "label": "WhatsApp consent",
            "field_type": "boolean",
            "required": True,
            "is_core": True,
        },
    ],
    "schedule_types": [
        {
            "key": "cylinder_delivery",
            "name": "Cylinder delivery",
            "fields": [
                {
                    "key": "booking_reference",
                    "label": "Booking reference",
                    "field_type": "text",
                    "required": True,
                    "is_core": True,
                    "max_length": 50,
                },
                {
                    "key": "booking_date",
                    "label": "Booking date",
                    "field_type": "date",
                    "required": True,
                    "is_core": True,
                },
                {
                    "key": "scheduled_date",
                    "label": "Planned delivery date",
                    "field_type": "date",
                    "required": False,
                    "is_core": True,
                },
                {
                    "key": "cylinder_type",
                    "label": "Cylinder type",
                    "field_type": "choice",
                    "required": True,
                    "is_core": False,
                    "options": [
                        "14.2 kg domestic",
                        "5 kg",
                        "10 kg composite",
                        "19 kg commercial",
                        "47.5 kg commercial",
                    ],
                },
                {
                    "key": "quantity",
                    "label": "Quantity",
                    "field_type": "number",
                    "required": True,
                    "is_core": False,
                },
                {
                    "key": "remarks",
                    "label": "Remarks",
                    "field_type": "text",
                    "required": False,
                    "is_core": True,
                    "max_length": 500,
                },
            ],
            "default_reminder_rule": {
                "send_time": "10:00",
                "days_before": 1,
                "wait_minutes": 240,
                "quiet_start": "21:00",
                "quiet_end": "08:00",
                "delivery_weekdays": DELIVERY_WEEKDAYS_MON_SAT,
                "channel_order": ["whatsapp"],
            },
            "default_question_flow": {
                "answers": [
                    {"key": "yes_available", "label": "Yes, available", "outcome": "confirmed"},
                    {"key": "not_available", "label": "Not available", "outcome": "not_available"},
                    {
                        "key": "change_date",
                        "label": "Change date",
                        "outcome": "reschedule",
                        "pick_next_delivery_days": 5,
                    },
                ]
            },
        }
    ],
}


def seed_template(template_model, schedule_type_model, field_model, version_model, definition):
    """Create the template at version 1 if it doesn't exist. Returns the template."""
    template = template_model.objects.filter(key=definition["key"]).first()
    if template is not None:
        return template
    template = template_model.objects.create(
        key=definition["key"],
        name=definition["name"],
        description=definition.get("description", ""),
        current_version=1,
    )
    for order, data in enumerate(definition["subscriber_fields"]):
        field_model.objects.create(template=template, entity="subscriber", order=order, **data)
    for st_order, st in enumerate(definition["schedule_types"]):
        schedule_type = schedule_type_model.objects.create(
            template=template,
            key=st["key"],
            name=st["name"],
            order=st_order,
            default_reminder_rule=st["default_reminder_rule"],
            default_question_flow=st["default_question_flow"],
        )
        for order, data in enumerate(st["fields"]):
            field_model.objects.create(
                template=template,
                schedule_type=schedule_type,
                entity="schedule",
                order=order,
                **data,
            )
    version_model.objects.create(template=template, version=1, definition=definition)
    return template


def seed_gas_agency():
    from .models import FieldDefinition, IndustryTemplate, IndustryTemplateVersion, ScheduleType

    return seed_template(
        IndustryTemplate, ScheduleType, FieldDefinition, IndustryTemplateVersion, GAS_AGENCY
    )
