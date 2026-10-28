"""
CSV import engine (Workflow 3.5). Reusable: each entity plugs in an adapter that knows its columns,
how to clean one row and how to upsert. Subscribers come first; bookings add an adapter in Step 14.

    upload  -> preview task: validate every row, count, write the error file (previewed)
    confirm -> confirm task: validate again, upsert valid rows by key, skip error rows (done)

Nothing is written to the entity tables before confirm, and confirm writes all rows or none.
"""

import csv
import datetime as dt
import io
import logging
import re
from dataclasses import dataclass, field

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.audit.models import Action
from apps.audit.services import write_audit
from apps.core.constants import Language
from apps.core.exceptions import ApiError
from apps.core.models import Tenant
from apps.core.tenancy import tenant_context
from apps.industry.fields import (
    FieldError,
    custom_field_definitions,
    is_blank,
    parse_boolean,
    parse_date,
    parse_phone,
    parse_value,
)
from apps.industry.services import tenant_config

from .models import Area, ImportJob, Subscriber

logger = logging.getLogger(__name__)

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 50_000
PREVIEW_ERRORS = 200  # rows of errors kept on the job for the preview screen
BATCH = 1000


@dataclass
class Column:
    key: str
    label: str
    required: bool = False
    aliases: tuple[str, ...] = ()


@dataclass
class CleanRow:
    key: str | None
    data: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)


def _norm(header: str) -> str:
    return re.sub(r"[\s\-/]+", "_", header.strip().lower()).strip("_")


class ImportFileError(Exception):
    """The file as a whole can't be imported (encoding, columns, size). Message is user-facing."""


# --- adapters -------------------------------------------------------------------------------


class SubscriberAdapter:
    entity = "subscribers"
    key_column = "external_ref"

    LANGUAGE_NAMES = {str(label).lower(): code for code, label in Language.choices}
    SOURCES = {value: value for value in Subscriber.ConsentSource.values} | {
        str(label).lower(): value for value, label in Subscriber.ConsentSource.choices
    }

    def columns(self, tenant) -> list[Column]:
        config = tenant_config(tenant)
        labels = {f["key"]: f["label"] for f in config["subscriber_fields"]}
        core = [
            Column("customer_name", labels.get("customer_name", "Customer name"), True),
            Column("mobile_number", labels.get("mobile_number", "Mobile number"), True),
            Column("alternate_number", labels.get("alternate_number", "Alternate number")),
            Column("external_ref", labels.get("external_ref", "Customer reference"), True),
            Column("preferred_language", labels.get("preferred_language", "Preferred language")),
            Column("address", labels.get("address", "Address")),
            Column("area", labels.get("area", "Area")),
            Column("whatsapp_consent", labels.get("whatsapp_consent", "WhatsApp consent"), True),
            Column("consent_date", _("Consent date")),
            Column("consent_source", _("Consent source")),
            Column("status", _("Status")),
        ]
        custom = [
            Column(f["key"], f["label"], f["required"])
            for f in custom_field_definitions(tenant, "subscriber")
        ]
        return core + custom

    def prepare(self, tenant) -> dict:
        areas = {}
        for area in Area.objects.filter(tenant=tenant):
            areas[area.name.lower()] = area
            if area.code:
                areas.setdefault(area.code.lower(), area)
        return {
            "tenant": tenant,
            "today": timezone.localdate(),
            "areas": areas,
            "custom": {f["key"]: f for f in custom_field_definitions(tenant, "subscriber")},
        }

    def clean(self, row: dict, ctx) -> CleanRow:
        data, errors = {}, {}

        def text(key, limit, required=False):
            value = (row.get(key) or "").strip()
            if not value:
                if required:
                    errors[key] = _("This field is required.")
                return
            if len(value) > limit:
                errors[key] = _("Up to %(limit)s characters.") % {"limit": limit}
                return
            data[key] = value

        def parsed(key, parser, required=False):
            raw = row.get(key)
            if is_blank(raw):
                if required:
                    errors[key] = _("This field is required.")
                return
            try:
                data[key] = parser(raw)
            except FieldError as exc:
                errors[key] = str(exc)

        text("customer_name", 100, required=True)
        parsed("mobile_number", parse_phone, required=True)
        parsed("alternate_number", parse_phone)
        text("external_ref", 50, required=True)
        text("address", 500)
        parsed("whatsapp_consent", parse_boolean, required=True)
        parsed("consent_date", parse_date)

        language = (row.get("preferred_language") or "").strip().lower()
        if language:
            code = language if language in Language.values else self.LANGUAGE_NAMES.get(language)
            if code:
                data["preferred_language"] = code
            else:
                errors["preferred_language"] = _("Use ml, en, hi or ta.")

        source = (row.get("consent_source") or "").strip().lower()
        if source:
            if source in self.SOURCES:
                data["consent_source"] = self.SOURCES[source]
            else:
                errors["consent_source"] = _(
                    "Use paper_form, verbal, whatsapp_opt_in, import or other."
                )

        status = (row.get("status") or "").strip().lower()
        if status:
            if status in Subscriber.Status.values:
                data["status"] = status
            else:
                errors["status"] = _("Use active or inactive.")

        area_name = (row.get("area") or "").strip()
        if area_name:
            area = ctx["areas"].get(area_name.lower())
            if area is None:
                errors["area"] = _("Unknown area.")
            elif not area.is_active:
                errors["area"] = _("This area is inactive.")
            else:
                data["area"] = area

        if data.get("consent_date") and data["consent_date"] > ctx["today"]:
            errors["consent_date"] = _("Can't be in the future.")
        if data.get("whatsapp_consent"):
            data.setdefault("consent_date", ctx["today"])
            data.setdefault("consent_source", Subscriber.ConsentSource.IMPORT)

        custom = {}
        for key, definition in ctx["custom"].items():
            raw = row.get(key)
            if is_blank(raw):
                if definition["required"]:
                    errors[key] = _("This field is required.")
                continue
            try:
                custom[key] = parse_value(definition, raw)
            except FieldError as exc:
                errors[key] = str(exc)
        if custom:
            data["custom_fields"] = custom

        return CleanRow(key=data.get("external_ref"), data=data, errors=errors)

    def existing_keys(self, tenant, keys) -> set[str]:
        found = set()
        keys = list(keys)
        for start in range(0, len(keys), BATCH):
            found.update(
                Subscriber.objects.filter(
                    tenant=tenant, external_ref__in=keys[start : start + BATCH]
                ).values_list("external_ref", flat=True)
            )
        return found

    UPDATABLE = [
        "customer_name",
        "mobile_number",
        "alternate_number",
        "preferred_language",
        "address",
        "area",
        "whatsapp_consent",
        "consent_date",
        "consent_source",
        "status",
    ]

    def upsert(self, tenant, rows: list[dict]) -> tuple[int, int]:
        """Create or update by external_ref. Blank cells leave stored values unchanged."""
        existing = {
            s.external_ref: s
            for s in Subscriber.objects.filter(
                tenant=tenant, external_ref__in=[r["external_ref"] for r in rows]
            )
        }
        now = timezone.now()
        to_create, to_update = [], []
        for data in rows:
            subscriber = existing.get(data["external_ref"])
            custom = data.pop("custom_fields", {})
            if subscriber is None:
                subscriber = Subscriber(
                    tenant=tenant, **{"preferred_language": tenant.default_language, **data}
                )
                subscriber.custom_fields = custom
                to_create.append(subscriber)
                continue
            for key, value in data.items():
                setattr(subscriber, key, value)
            if data.get("whatsapp_consent") is False:
                subscriber.consent_date, subscriber.consent_source = None, ""
            subscriber.custom_fields = {**subscriber.custom_fields, **custom}
            subscriber.updated_at = now
            to_update.append(subscriber)
        Subscriber.objects.bulk_create(to_create, batch_size=BATCH)
        Subscriber.objects.bulk_update(
            to_update, [*self.UPDATABLE, "custom_fields", "updated_at"], batch_size=BATCH
        )
        return len(to_create), len(to_update)


ADAPTERS = {adapter.entity: adapter for adapter in [SubscriberAdapter()]}
# Bookings register their adapter from apps.schedules (AppConfig.ready).


def register_adapter(adapter):
    ADAPTERS[adapter.entity] = adapter


def adapter_for(entity: str):
    try:
        return ADAPTERS[entity]
    except KeyError:
        raise ApiError("validation_error", _("Unknown import type."), 400) from None


# --- template ------------------------------------------------------------------------------


def template_csv(tenant, entity: str) -> str:
    """Header row built from the industry template's active fields (BOM so Excel reads UTF-8)."""
    buffer = io.StringIO()
    csv.writer(buffer).writerow([c.key for c in adapter_for(entity).columns(tenant)])
    return "﻿" + buffer.getvalue()


# --- reading -------------------------------------------------------------------------------


def read_rows(job: ImportJob, columns: list[Column]):
    """
    Parse the uploaded file. Returns (header, mapping warnings, rows) where rows is a list of
    (line_number, original_cells, {column_key: value}). Raises ImportFileError.
    """
    with job.file.open("rb") as handle:
        raw = handle.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFileError(
            _("The file is not UTF-8. Save it as “CSV UTF-8” and upload again.")
        ) from None
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise ImportFileError(_("The file is empty.")) from None
    except csv.Error as exc:
        raise ImportFileError(
            _("The file is not a valid CSV: %(error)s") % {"error": exc}
        ) from None

    lookup = {}
    for column in columns:
        for name in (column.key, column.label, *column.aliases):
            lookup.setdefault(_norm(name), column.key)
    positions, seen, ignored = {}, set(), []
    for index, cell in enumerate(header):
        key = lookup.get(_norm(cell))
        if key is None:
            if cell.strip():
                ignored.append(cell.strip())
            continue
        if key in seen:
            raise ImportFileError(
                _("Column “%(column)s” appears twice.") % {"column": cell.strip()}
            )
        seen.add(key)
        positions[key] = index
    missing = [c.key for c in columns if c.required and c.key not in positions]
    if missing:
        raise ImportFileError(
            _("Missing required columns: %(columns)s.") % {"columns": ", ".join(missing)}
        )

    rows = []
    try:
        for line_number, cells in enumerate(reader, start=2):
            if not any(cell.strip() for cell in cells):
                continue
            if len(rows) >= MAX_ROWS:
                raise ImportFileError(
                    _("The file has more than %(max)s rows. Split it into smaller files.")
                    % {"max": f"{MAX_ROWS:,}"}
                )
            mapped = {key: cells[i] if i < len(cells) else "" for key, i in positions.items()}
            rows.append((line_number, cells, mapped))
    except csv.Error as exc:
        raise ImportFileError(
            _("The file is not a valid CSV: %(error)s") % {"error": exc}
        ) from None
    return header, ignored, rows


def validate(job: ImportJob):
    """Clean every row. Returns (header, ignored columns, valid rows, error rows)."""
    adapter = adapter_for(job.entity)
    tenant = job.tenant
    header, ignored, rows = read_rows(job, adapter.columns(tenant))
    ctx = adapter.prepare(tenant)
    first_seen = {}
    valid, errors = [], []
    for line_number, cells, mapped in rows:
        result = adapter.clean(mapped, ctx)
        if result.key and not result.errors:
            if result.key in first_seen:
                result.errors[adapter.key_column] = _(
                    "Duplicate %(column)s in this file (first on row %(row)s)."
                ) % {"column": adapter.key_column, "row": first_seen[result.key]}
            else:
                first_seen[result.key] = line_number
        if result.errors:
            errors.append((line_number, cells, result))
        else:
            valid.append(result.data)
    return header, ignored, valid, errors


def _write_error_file(job: ImportJob, header, errors):
    if not errors:
        if job.error_file:
            job.error_file.delete(save=False)
        job.error_file = ""
        return
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([*header, "error"])
    for _line, cells, result in errors:
        message = "; ".join(f"{column}: {text}" for column, text in result.errors.items())
        writer.writerow([*cells, message])
    job.error_file.save("errors.csv", ContentFile(("﻿" + buffer.getvalue()).encode()), save=False)


def _preview_errors(errors):
    return [
        {"row": line, "key": result.key or "", "errors": result.errors}
        for line, _cells, result in errors[:PREVIEW_ERRORS]
    ]


# --- jobs ----------------------------------------------------------------------------------


def start_import(tenant, entity: str, upload, user) -> ImportJob:
    adapter_for(entity)
    name = upload.name or "upload.csv"
    if not name.lower().endswith(".csv"):
        raise serializers.ValidationError({"file": [_("Upload a .csv file.")]})
    if upload.size > MAX_BYTES:
        raise serializers.ValidationError(
            {"file": [_("The file is larger than 20 MB. Split it into smaller files.")]}
        )
    job = ImportJob.objects.create(
        tenant=tenant,
        entity=entity,
        original_filename=name[:255],
        created_by=user,
        status=ImportJob.Status.PREVIEWING,
    )
    job.file.save("source.csv", upload, save=True)
    write_audit(
        Action.CREATE,
        job,
        after={"entity": entity, "file": job.original_filename, "size": upload.size},
    )
    from .tasks import preview_import

    transaction.on_commit(
        lambda: preview_import.delay(job_id=str(job.pk), tenant_id=str(job.tenant_id))
    )
    return job


def _fail(job: ImportJob, reason: str):
    job.status = ImportJob.Status.FAILED
    job.failure_reason = reason
    job.finished_at = timezone.now()
    job.save()


def run_preview(job: ImportJob) -> ImportJob:
    if job.status not in (ImportJob.Status.UPLOADED, ImportJob.Status.PREVIEWING):
        return job
    try:
        with transaction.atomic():
            header, ignored, valid, errors = validate(job)
            existing = adapter_for(job.entity).existing_keys(
                job.tenant, [row[adapter_for(job.entity).key_column] for row in valid]
            )
            job.total_rows = len(valid) + len(errors)
            job.valid_rows = len(valid)
            job.error_rows = len(errors)
            job.errors = _preview_errors(errors)
            job.summary = {
                "would_create": len(valid) - len(existing),
                "would_update": len(existing),
                "ignored_columns": ignored,
            }
            _write_error_file(job, header, errors)
            job.status = ImportJob.Status.PREVIEWED
            job.previewed_at = timezone.now()
            job.save()
    except ImportFileError as exc:
        _fail(job, str(exc))
    except Exception:
        logger.exception("Import preview failed: %s", job.pk)
        _fail(job, _("Something went wrong while checking the file. Please try again."))
    return job


def request_confirm(job_id, user) -> ImportJob:
    """Move a previewed import to confirming (once) and queue the import."""
    job = ImportJob.objects.select_for_update().get(pk=job_id)
    if job.status != ImportJob.Status.PREVIEWED:
        raise ApiError("invalid_state", _("This import can't be confirmed now."), 409)
    if job.valid_rows == 0:
        raise ApiError("nothing_to_import", _("No valid rows to import."), 409)
    job.status = ImportJob.Status.CONFIRMING
    job.confirmed_by = user
    job.confirmed_at = timezone.now()
    job.save()
    from .tasks import confirm_import

    transaction.on_commit(
        lambda: confirm_import.delay(job_id=str(job.pk), tenant_id=str(job.tenant_id))
    )
    return job


def run_confirm(job: ImportJob) -> ImportJob:
    if job.status != ImportJob.Status.CONFIRMING:
        return job
    adapter = adapter_for(job.entity)
    try:
        with transaction.atomic():
            # Validate again: areas or existing subscribers may have changed since the preview.
            header, _ignored, valid, errors = validate(job)
            created = updated = 0
            for start in range(0, len(valid), BATCH):
                c, u = adapter.upsert(job.tenant, valid[start : start + BATCH])
                created, updated = created + c, updated + u
            job.total_rows = len(valid) + len(errors)
            job.valid_rows, job.error_rows = len(valid), len(errors)
            job.created_rows, job.updated_rows = created, updated
            job.errors = _preview_errors(errors)
            _write_error_file(job, header, errors)
            job.status = ImportJob.Status.DONE
            job.finished_at = timezone.now()
            job.save()
            write_audit(
                Action.IMPORT,
                job,
                after={
                    "entity": job.entity,
                    "file": job.original_filename,
                    "total_rows": job.total_rows,
                    "created": created,
                    "updated": updated,
                    "skipped": len(errors),
                },
                actor=job.confirmed_by,
            )
    except ImportFileError as exc:
        _fail(job, str(exc))
    except Exception:
        logger.exception("Import confirm failed: %s", job.pk)
        _fail(job, _("Something went wrong while importing. Nothing was saved. Please try again."))
    return job


STALE_AFTER = dt.timedelta(minutes=30)


def fail_stale_imports() -> int:
    """
    Fail imports stuck in a background step (lost task, worker restart) so the user can upload
    again instead of waiting forever. Runs from Celery beat, one tenant at a time (RLS).
    """
    cutoff = timezone.now() - STALE_AFTER
    failed = 0
    for tenant_id in Tenant.objects.values_list("pk", flat=True):
        with tenant_context(tenant_id):
            stale = ImportJob.objects.filter(
                status__in=[ImportJob.Status.PREVIEWING, ImportJob.Status.CONFIRMING],
                updated_at__lt=cutoff,
            )
            for job in stale:
                _fail(
                    job,
                    _(
                        "This import stopped before it finished. Nothing was saved. "
                        "Upload the file again."
                    ),
                )
                failed += 1
    return failed
