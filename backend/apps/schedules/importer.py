"""Bookings adapter for the CSV import engine (Workflow 3.5): upsert by booking_reference."""

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.industry.fields import (
    FieldError,
    custom_field_definitions,
    is_blank,
    parse_date,
    parse_value,
)
from apps.industry.models import ScheduleType
from apps.industry.services import tenant_config
from apps.subscribers.importer import BATCH, CleanRow, Column
from apps.subscribers.models import Subscriber

from . import services
from .hooks import on_schedule_date_changed
from .models import Schedule, ScheduleEvent
from .serializers import QUANTITY_RANGE


class BookingAdapter:
    entity = "bookings"
    key_column = "booking_reference"

    def schedule_type(self, tenant):
        return (
            ScheduleType.objects.filter(template_id=tenant.industry_template_id, is_active=True)
            .order_by("order", "key")
            .first()
        )

    def columns(self, tenant) -> list[Column]:
        config = tenant_config(tenant)
        st = self.schedule_type(tenant)
        fields = next((t["fields"] for t in config["schedule_types"] if t["key"] == st.key), [])
        labels = {f["key"]: f["label"] for f in fields}
        sub_labels = {f["key"]: f["label"] for f in config["subscriber_fields"]}
        core = [
            Column("booking_reference", labels.get("booking_reference", "Booking reference"), True),
            Column(
                "subscriber_ref",
                sub_labels.get("external_ref", "Customer reference"),
                True,
                aliases=("external_ref",),
            ),
            Column("booking_date", labels.get("booking_date", "Booking date"), True),
            Column("scheduled_date", labels.get("scheduled_date", "Delivery date")),
        ]
        custom = [
            Column(f["key"], f["label"], f["required"])
            for f in custom_field_definitions(tenant, "schedule", st.key)
        ]
        return [*core, *custom, Column("remarks", labels.get("remarks", "Remarks"))]

    def prepare(self, tenant) -> dict:
        st = self.schedule_type(tenant)
        return {
            "tenant": tenant,
            "today": timezone.localdate(),
            "schedule_type": st,
            "subscribers": dict(
                Subscriber.objects.filter(tenant=tenant).values_list("external_ref", "id")
            ),
            "custom": {f["key"]: f for f in custom_field_definitions(tenant, "schedule", st.key)},
        }

    def clean(self, row: dict, ctx) -> CleanRow:
        data, errors = {}, {}
        reference = (row.get("booking_reference") or "").strip()
        if not reference:
            errors["booking_reference"] = _("This field is required.")
        elif len(reference) > 50:
            errors["booking_reference"] = _("Up to %(limit)s characters.") % {"limit": 50}
        else:
            data["booking_reference"] = reference

        ref = (row.get("subscriber_ref") or "").strip()
        if not ref:
            errors["subscriber_ref"] = _("This field is required.")
        elif ref not in ctx["subscribers"]:
            errors["subscriber_ref"] = _("No subscriber has this reference.")
        else:
            data["subscriber_id"] = ctx["subscribers"][ref]

        for key, required in (("booking_date", True), ("scheduled_date", False)):
            raw = row.get(key)
            if is_blank(raw):
                if required:
                    errors[key] = _("This field is required.")
                continue
            try:
                data[key] = parse_date(raw)
            except FieldError as exc:
                errors[key] = str(exc)
        if data.get("booking_date") and data["booking_date"] > ctx["today"]:
            errors["booking_date"] = _("Can't be in the future.")
        if data.get("scheduled_date") and data["scheduled_date"] < ctx["today"]:
            errors["scheduled_date"] = _("Can't be in the past.")

        remarks = (row.get("remarks") or "").strip()
        if len(remarks) > 500:
            errors["remarks"] = _("Up to %(limit)s characters.") % {"limit": 500}
        elif remarks:
            data["remarks"] = remarks

        custom = {}
        for key, definition in ctx["custom"].items():
            raw = row.get(key)
            if is_blank(raw):
                if key == "quantity":
                    custom[key] = 1
                elif definition["required"]:
                    errors[key] = _("This field is required.")
                continue
            try:
                custom[key] = parse_value(definition, raw)
            except FieldError as exc:
                errors[key] = str(exc)
        low, high = QUANTITY_RANGE
        quantity = custom.get("quantity")
        if quantity is not None and (not isinstance(quantity, int) or not low <= quantity <= high):
            errors["quantity"] = _("A whole number from 1 to 99.")
        data["custom_fields"] = custom
        return CleanRow(key=data.get("booking_reference"), data=data, errors=errors)

    def existing_keys(self, tenant, keys) -> set[str]:
        keys, found = list(keys), set()
        for start in range(0, len(keys), BATCH):
            found.update(
                Schedule.objects.filter(
                    tenant=tenant, booking_reference__in=keys[start : start + BATCH]
                ).values_list("booking_reference", flat=True)
            )
        return found

    def upsert(self, tenant, rows: list[dict]) -> tuple[int, int]:
        """
        New bookings are created (with a timeline entry); existing ones get the file's values.
        A new delivery date on an existing booking goes through set_date, so the cycle restarts.
        The subscriber of an existing booking is never changed by an import.
        """
        existing = {
            s.booking_reference: s
            for s in Schedule.objects.filter(
                tenant=tenant, booking_reference__in=[r["booking_reference"] for r in rows]
            )
        }
        schedule_type = self.schedule_type(tenant)
        to_create, updated = [], 0
        now = timezone.now()
        for data in rows:
            schedule = existing.get(data["booking_reference"])
            if schedule is None:
                to_create.append(Schedule(tenant=tenant, schedule_type=schedule_type, **data))
                continue
            new_date = data.pop("scheduled_date", None)
            data.pop("subscriber_id", None)
            custom = data.pop("custom_fields", {})
            for key, value in data.items():
                setattr(schedule, key, value)
            schedule.custom_fields = {**schedule.custom_fields, **custom}
            schedule.updated_at = now
            schedule.save()
            if (
                new_date
                and new_date != schedule.scheduled_date
                and schedule.status != Schedule.Status.CANCELLED
            ):
                services.set_date(schedule, new_date, reason="import")
            updated += 1
        Schedule.objects.bulk_create(to_create, batch_size=BATCH)
        ScheduleEvent.objects.bulk_create(
            [
                ScheduleEvent(tenant=tenant, schedule=s, kind="created", data={"source": "import"})
                for s in to_create
            ],
            batch_size=BATCH,
        )
        for schedule in to_create:
            if schedule.scheduled_date:
                on_schedule_date_changed(schedule, previous_date=None)
        return len(to_create), updated
