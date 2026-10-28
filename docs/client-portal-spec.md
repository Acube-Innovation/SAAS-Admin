# Client Portal — Specification

> **Derived document.** Written from the *System Architecture* doc, the *Functional Spec (Pilot MVP)* and the
> *Claude Code Build Prompts* (27 Oct 2026). Replace it with the official Client Portal guide when that is
> available. Shared rules (login, 2FA, invites, audit, tenancy) are in `docs/admin-console-spec.md` and are not
> repeated here.

The client portal (`app.<domain>`) is where a client agency's team does its daily work: bookings in the morning,
the delivery list in the evening, and the follow-up queue in between. Screen labels such as "Bookings",
"Deliveries" and "Cylinder type" come from the tenant's **industry template**, so other industries get their own
wording without code changes.

---

## 1. Screens

| # | Screen | Purpose |
| --- | --- | --- |
| 1 | Home | Today and tomorrow at a glance (2.1) |
| 2 | Subscribers | Customer master, CSV import (2.2) |
| 3 | Bookings | Dated schedules, bulk set date, cancel, timeline (2.3) |
| 4 | Deliveries | Delivery list by date and area (2.3) |
| 5 | Follow-up queue | Exceptions to work (2.4) |
| 6 | Reports | Reminder results, follow-up ageing, subscriber health, message log (2.5) |
| 7 | Messages and voice | View-only templates, voice scripts and question flows; approvals; change requests (2.6) |
| 8 | Areas and staff | Areas / routes with default staff (2.7) |
| 9 | Communication log | Every message and call, with transcript and recording (2.8) |
| 10 | Wallet and billing / Plan | Balance, top-up, auto top-up, invoices; plan (view-only) (2.9) |
| 11 | Users | Team access (2.10) |
| 12 | Reminder settings | The agency's reminder rule (2.10) |
| 13 | Integrations | API keys, webhooks (2.10) |
| 14 | Audit log | Owner's view of the agency's audit log (Step 23) |

### 1.1 Menu and permissions by role

| Screen / action | owner | manager | office_staff | platform support (support session) |
| --- | --- | --- | --- | --- |
| Home | Yes | Yes | Yes | View |
| Subscribers: view / add / edit / import | Yes | Yes | Yes | View |
| Bookings: view / add / edit / bulk date / cancel | Yes | Yes | Yes | View |
| Deliveries: view / reassign / print / CSV | Yes | Yes | View + print | View |
| Follow-up queue: work items | Yes | Yes | Yes | View |
| Reports + CSV export | Yes | Yes | No | View |
| Messages and voice: view | Yes | Yes | Yes | View |
| Approve / send back drafts; raise change requests | Yes | Yes | No | No |
| Areas and staff | Yes | Yes | No | View |
| Communication log (play recordings) | Yes | Yes | View (no recordings) | View |
| Wallet and billing / Plan | Yes | View | No | View |
| Users | Yes | No | No | View |
| Reminder settings | Yes | Yes | No | View |
| Integrations | Yes | No | No | No |
| Audit log | Yes | No | No | View |

Screens a role can't use are hidden from its menu, and their APIs return `403`. A **suspended** tenant sees a
read-only banner: every write returns `403 tenant_suspended`.

---

## 2. Screens and fields

### 2.1 Home

- Counts for **tomorrow's** bookings: confirmed, reschedule, not available, no response, awaiting reply, needs review.
- Follow-up queue size, with the oldest item's age.
- Bookings with no delivery date yet.
- Alerts: sending stopped (template paused, send cap reached, client suspended, wallet empty) and held reminders.
- Wallet balance and low-balance warning (owner only).
- Refreshes every **30 seconds**.
- **First-login checklist** (Workflow 3.6) until it is complete.

### 2.2 Subscribers

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `customer_name` | text | Yes | Up to 100 characters |
| `mobile_number` | phone | Yes | Indian mobile, stored `+91` + 10 digits (starting 6–9) |
| `alternate_number` | phone | No | Same rules. Not messaged in the pilot |
| `external_ref` | text | Yes | The industry's customer key (gas: **consumer number**). **Unique within the tenant**. Used for import upserts |
| `preferred_language` | choice | Yes | `ml`, `en` (pilot), `hi`, `ta`. Defaults to the tenant's default language |
| `address` | text | No | Free text, up to 500 characters |
| `area` | FK Area | No | The tenant's areas |
| `whatsapp_consent` | boolean | Yes | Reminders go only when true |
| `consent_date`, `consent_source` | date, choice | When consent is true | `consent_source`: `paper_form`, `verbal`, `whatsapp_opt_in`, `import`, `other` |
| `opted_out`, `opted_out_at` | system | — | Set when the customer replies STOP. **The agency can't clear it**. Only a new opt-in from the customer does |
| `status` | choice | Yes | `active`, `inactive`. Inactive subscribers are never messaged |
| `custom_fields` | JSONB | per template | Validated against the industry template's active subscriber field definitions |

List: search by name, mobile or `external_ref`; filter by area, status, consent, opted out; 50 rows per page;
consent and opt-out badges per row. Actions: add, edit, deactivate, import CSV.

### 2.3 Bookings and Deliveries

**Booking** (a `Schedule` of the tenant's schedule type, e.g. "Cylinder delivery"):

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `booking_reference` | text | Yes | Unique within the tenant. Used for import upserts |
| `subscriber` | FK | Yes | Picked by `external_ref` or mobile |
| `booking_date` | date | Yes | **Not in the future** |
| `scheduled_date` | date | No | Planned delivery date. **Optional**. Today or later. No reminder until set. Changing it restarts the reminder cycle |
| `custom_fields.cylinder_type` | choice | Yes | The tenant's cylinder types |
| `custom_fields.quantity` | integer | Yes | Default 1, 1–99 |
| `status` | system | — | `booked`, `reminded`, `awaiting_reply`, `closed`, `cancelled` |
| `outcome` | system | — | `confirmed`, `reschedule`, `not_available`, `needs_review`, `no_response`, `failed`, `opted_out`, `skipped` |
| `new_date_requested` | date | — | Set when the customer picks another day |
| `remarks` | text | No | Up to 500 characters. Staff only |

Bookings list: by `scheduled_date`, with a **"No date yet"** filter, outcome and status filters, and search by
reference, name or mobile. Actions: add, edit, **bulk set date** (selected rows, one date), **cancel** (with
reason), import CSV. The **timeline drawer** shows every status change, reminder attempt, reply and note in order.

**Deliveries:** confirmed bookings for a chosen date (default: next delivery day), grouped by **area**. Each
booking has a **Job** assigned to the area's default staff. Actions: reassign staff, print (clean driver list),
CSV export. Non-confirmed bookings appear in separate tabs. Outcome changes after printing are flagged.

### 2.4 Follow-up queue

Every booking whose outcome is not Confirmed: `reschedule`, `not_available`, `needs_review` (with the message
text), `no_response`, `failed` (with the reason), `opted_out`.

Actions: **set new date** (restarts the reminder cycle), **mark contacted**, **cancel booking**, **add note**,
**resolve**. Ageing colours: under 4 h normal, 4–24 h amber, over 24 h red. Filters: outcome, area, age.

### 2.5 Reports

| Report | Shows | Filters |
| --- | --- | --- |
| Reminder results | Sent, delivered, replied, each outcome as count and share | Date range, area, cylinder type |
| Follow-up ageing | Open follow-up items by waiting time | Area, outcome |
| Subscriber health | No consent, opted out, failed numbers | Area |
| Message log | Every message and reply with time and status | Date range, subscriber, booking |

CSV export for each report. Exports are audited.

### 2.6 Messages and voice (view-only), approvals, change requests

- **View-only** list of the tenant's WhatsApp templates (live version per language) shown in a **WhatsApp phone
  preview** with sample values, voice scripts shown as a script view, and question flows (answers → outcomes).
  **Clients have no create or edit API for content**: such attempts return `403` / `405`.
- **Approvals:** versions in `sent_to_client` show **Approve** and **Send back** (comment required, 5–1,000
  characters). Owner and manager only.
- **Change requests:** raise a request (`request_type`, `subject`, `description`), see its status, and comment.
  See admin spec 2.9 and Workflow 3.3 below.

### 2.7 Areas and staff

| Field | Rules |
| --- | --- |
| `name` | 2–100 characters, unique within the tenant |
| `code` | Optional, up to 20 characters |
| `default_staff` | FK `StaffMember`. Jobs in this area are auto-assigned to them |
| `is_active` | Inactive areas can't be picked |

**StaffMember** (delivery / collection people, not portal users): `name`, `phone`, `is_active`.

### 2.8 Communication log

Every `Attempt` (message or call) with subscriber, booking, channel, time, delivery status and cost, plus its
`Response`s. Transcript view for calls. Recording player using **S3 presigned links valid for 5 minutes**. Every
play is audited. Recordings expire after 90 days (S3 lifecycle rule).

### 2.9 Wallet and billing / Plan

- Balance in rupees (stored in **paise**), transactions, invoices (GST PDF, financial-year numbering, e.g.
  `SC/2026-27/000123`).
- **Low-balance level** = 3 × average daily usage of the last 14 days, or ₹1,000 until there are 14 days of history.
- **Top-up:** minimum ₹1,000. Credited **only from the signed payment webhook**, never from the browser redirect.
- **Auto top-up:** UPI AutoPay or card mandate, ₹1,000–₹15,000 per debit, triggered at the low-balance level.
  Pause and cancel are available.
- At zero balance, sending pauses: reminders are held and skipped on resume when their send time has passed.
- **Plan:** view-only, with a **Request a plan change** button that raises a change request.

### 2.10 Users, Reminder settings, Integrations

**Users** (owner only): invite (email, name, role `manager` / `office_staff` / `owner`), change role, deactivate,
resend invite. The last active owner can't be removed or demoted.

**Reminder settings** (owner, manager):

| Field | Default | Rules |
| --- | --- | --- |
| `send_time` | 10:00 | HH:MM |
| `days_before` | 1 | 0–7 |
| `wait_minutes` | 240 | 30–1440 |
| `quiet_start` / `quiet_end` | 21:00 / 08:00 | No messages in this window |
| `default_language` | tenant default | `ml`, `en`, `hi`, `ta` |
| `delivery_weekdays` | Mon–Sat | At least one day |
| `holiday_dates` | [] | Future dates |
| Linked templates | — | Read-only |

Changes apply to reminders not yet sent.

**Integrations** (owner): API keys (shown **once**, stored hashed, revocable at once), webhook endpoint URL (HTTPS)
with a **test event**, HMAC-SHA256 signing secret, delivery log (retries for 24 h).

---

## 3. Workflows

### 3.1 Booking to delivery

1. A booking is created (portal or import). With no `scheduled_date` it waits.
2. When a date is set, the reminder engine (Phase 2) schedules the reminder for `days_before` at `send_time`, outside
   quiet hours and on delivery days.
3. The reply sets the outcome. **Confirmed** creates a `Job` for that date, auto-assigned to the area's
   `default_staff` (reassignable).
4. The booking appears on **Deliveries** for that date.
5. Any other outcome creates a **FollowUpItem** (Workflow 3.2).

### 3.2 Follow-up

- `reschedule`: the booking moves to `new_date_requested`, and the reminder cycle restarts through the hook
  `on_schedule_date_changed(schedule)`.
- `not_available` / `no_response` / `failed` / `needs_review` / `opted_out`: a FollowUpItem is opened.
- Actions: set new date (restarts the cycle and resolves the item), mark contacted (stays open, logged), cancel
  booking (resolves, no further messages), add note, resolve.
- A later reply on the same booking replaces the outcome (latest wins). History keeps every answer.

Implementation notes (Step 14):
- All booking state changes go through `apps.schedules.services`; each writes a timeline event and an audit entry.
- A new delivery date (set date, bulk set date, edit, follow-up "set new date", import, customer reschedule) clears
  the outcome, sets the status back to `booked`, cancels a pending job for the old date, resolves an open follow-up
  (`new_date`) and calls `on_schedule_date_changed(schedule)` (`apps.schedules.hooks`; the reminder engine connects
  to the `schedule_date_changed` signal).
- `reschedule` with a picked day in the future moves the booking there; without a day it opens a follow-up item.
- A non-confirmed answer after a confirmed one cancels the pending job; a later "yes" resolves the follow-up item
  (`confirmed`) and creates a new job. `opted_out` also marks the subscriber opted out. `skipped` opens no item.
- At most one live job and one unresolved follow-up item per booking (database constraints).
- Cancelled bookings take no further changes (`409 booking_cancelled`); resolved items take no actions
  (`409 follow_up_resolved`).

### 3.3 Content approval and change requests

1. Platform staff send a template version to the client (admin 3.4): it shows in **Messages and voice** with
   Approve / Send back.
2. **Approve** → `signed_off` (recorded with user and time). **Send back** → `sent_back` with the comment.
3. The client can raise a **change request** at any time. Staff work it in the admin queue (admin 3.6).
4. When the linked version goes live, the request is Done and the client sees the new live version.

### 3.4 Wallet pause and resume

1. Every send debits the wallet (row-locked; the balance never goes below zero).
2. Below the low-balance level: alert, plus an auto top-up if a mandate is active.
3. At zero: `tenant.can_send()` is False and reminders are **held**.
4. After a credit: sending resumes, and held reminders whose send time has passed are marked **Skipped**.

### 3.5 CSV import (subscribers, bookings)

1. Download the template. Columns are built from the industry template's active fields.
2. Upload (CSV, UTF-8, up to 20 MB / 50,000 rows). A **background preview job** validates every row.
3. The preview shows totals and **per-row errors** (missing field, bad phone, duplicate `external_ref` /
   `booking_reference` in the file, unknown area, bad date).
4. **Nothing is saved until the user confirms**. Confirm upserts valid rows **by `external_ref`** (subscribers) or
   `booking_reference` (bookings) and skips error rows.
5. An **error file** (original columns + `error` column) is available to download.
6. The import is audited with counts.

Bookings import: columns `booking_reference`, `subscriber_ref` (the subscriber's `external_ref`), `booking_date`,
`scheduled_date`, the schedule type's custom fields (gas: `cylinder_type`, `quantity` default 1, 1–99) and `remarks`.
Booking date not in the future, delivery date not in the past. A new delivery date on an existing booking restarts
its reminder cycle; an import never changes an existing booking's subscriber.

Import details (subscribers):
- Headers match a field's key or its label, ignoring case and spacing (`Consumer number` = `external_ref`).
  Unknown columns are ignored and listed in the preview. A missing required column or a repeated column fails the
  whole file, as do a non-UTF-8 file and more than 50,000 rows.
- Values: phones as in 2.2; dates `YYYY-MM-DD` or day first (`DD-MM-YYYY`, `DD/MM/YYYY`); yes/no as `yes`, `y`,
  `true`, `1` / `no`, `n`, `false`, `0`; language as a code or name; area by name or code (must be active).
- `whatsapp_consent = yes` without `consent_date` / `consent_source` records the import day and `import`.
- Updates (existing `external_ref`): a blank cell leaves the stored value unchanged. An import never clears
  `opted_out`. Custom fields are merged.
- Confirm validates every row again (areas may have changed since the preview) and writes all valid rows in one
  transaction: either all of them are saved or none. Imports stuck in a background step for 30 minutes are failed
  (nothing saved) so the user can upload again.

### 3.6 First login (owner)

1. Accept invite → set password → set up 2FA.
2. Checklist on Home: confirm agency details → add areas → set reminder settings → import subscribers → import
   bookings → invite team → **set up auto top-up (skippable)**.
3. Each item ticks when done. The checklist hides when everything is done or skipped.

---

## 4. Non-functional requirements

Same as admin spec Section 4. Also: a reply shows in the portal **within 1 minute**. Pages respond within 2 s at
50,000 subscribers. The portal works on desktop and mobile browsers (latest Chrome, Edge, Safari, Firefox).

## 5. Repository

The portal lives in the **SAAS-Client** repository (`apps/portal`), with its own copy of `packages/ui`,
`packages/api-client` and `packages/config`. Its backend apps live in SAAS-Admin's `backend/`: see admin spec
Section 5.

## 6. Data model (portal additions)

All are tenant-scoped (`tenant_id` + RLS) with UUID keys.

| Model | App | Key fields |
| --- | --- | --- |
| Area | subscribers | `name`, `code`, `default_staff`, `is_active` |
| StaffMember | jobs | `name`, `phone`, `is_active` |
| Subscriber | subscribers | see 2.2 |
| ImportJob | subscribers | `entity`, `file` (S3), `status` (`uploaded`, `previewing`, `previewed`, `confirming`, `done`, `failed`), `total_rows`, `valid_rows`, `error_rows`, `created_rows`, `updated_rows`, `errors` (first 200 rows; the error file has all), `summary` (`would_create`, `would_update`, `ignored_columns`), `error_file` (S3), `failure_reason`, `created_by`, `confirmed_by` |
| Schedule | schedules | see 2.3 (+ `schedule_type`, `cancelled_at`, `cancel_reason`) |
| ScheduleEvent | schedules | timeline entries: `kind`, `occurred_at`, `data`, `actor` |
| Attempt | schedules | `schedule`, `channel` (`whatsapp`, `voice`), `sent_at`, `provider_message_id` (unique when set), `delivery_status`, `failure_reason`, `cost_paise` |
| Response | schedules | `attempt`, `received_at`, `raw_text`, `button_payload`, `transcript`, `recording_key`, `outcome`, `confidence` |
| Job | jobs | `schedule`, `scheduled_date`, `assigned_staff`, `status` (`pending`, `done`, `cancelled`), `cancelled_reason` |
| FollowUpItem | jobs | `schedule`, `outcome`, `status` (`open`, `contacted`, `resolved`), `notes` (list of `at`, `by`, `text`), `opened_at`, `contacted_at`, `resolved_at`, `resolution` |
| ChangeRequest / ChangeRequestComment | change_requests | admin spec 2.9 |
| Wallet / WalletTransaction / Invoice / PaymentMandate | billing | paise amounts, `idempotency_key` / provider event id unique |
| ApiKey / WebhookEndpoint / WebhookDelivery | integrations | `key_hash`, `prefix`, `revoked_at`; `url`, `secret`; `attempts`, `status` |

## 7. Portal API

Under `/api/portal/`, scoped to the current tenant (from membership or a support session). Public API for clients'
systems under `/api/v1/` (API key auth, per-key rate limits): subscribers, schedules, outcomes.

| Method | Path | Purpose | Roles (writes) |
| --- | --- | --- | --- |
| GET | `config` | Tenant name, default language, industry fields (labels, choices) and schedule types | all |
| GET POST PATCH | `subscribers` · `subscribers/{id}` | List (search, `area` / `none`, `status`, `consent`, `opted_out`; 50 per page), add, edit | all |
| POST | `subscribers/{id}/deactivate` | Set inactive (there is no delete) | all |
| GET POST PATCH | `areas` · `areas/{id}` | List (`active=true`; with subscriber counts), add, edit / deactivate | read: all · write: owner, manager |
| GET POST PATCH | `staff` · `staff/{id}` | Delivery staff | owner, manager |
| GET | `imports/template?entity=subscribers\|bookings` | CSV header from the industry template's active fields | all |
| GET POST | `imports` · `imports/{id}` | Upload (multipart `entity`, `file`) and follow the preview | all |
| POST | `imports/{id}/confirm` | Import the valid rows (once, after the preview) | all |
| GET | `imports/{id}/error-file` | Original columns + `error` for every rejected row | all |
| GET POST PATCH | `bookings` · `bookings/{id}` | List (`date`, `date_from`/`date_to`, `no_date`, `status`, `outcome` / `none`, `area`, `search`; 50 per page), add (subscriber by id or `subscriber_ref`), edit (a new `scheduled_date` goes through set-date) | all |
| POST | `bookings/{id}/set-date` · `bookings/bulk-set-date` | Give one or many bookings a delivery date (restarts the reminder cycle; cancelled ones are skipped) | all |
| POST | `bookings/{id}/cancel` | Cancel with a reason (5–500 characters) | all |
| GET | `bookings/{id}/timeline` | Events, reminder attempts and replies, oldest first | all |
| GET | `deliveries?date=` | Confirmed bookings for a date (default: next delivery day) grouped by area, plus counts of the other outcomes | all |
| POST | `jobs/{id}/reassign` | Change the staff member of a pending delivery | owner, manager |
| GET | `follow-ups` · `follow-ups/{id}` | Open and contacted items (`status`, `outcome`, `area`, `age` = `normal` / `amber` / `red`), oldest first, with the last reply and failure reason | all |
| POST | `follow-ups/{id}/set-date` · `contacted` · `note` · `cancel-booking` · `resolve` | Workflow 3.2 actions | all |

Platform staff in a support session can read everything above and change nothing. A suspended tenant reads only.

## 8. Rules

- Message content (templates, voice scripts, question flows) is **view-only** for clients. There are no create or
  edit routes; the only content actions are approve / send back on drafts and change requests.
- Every write is audited (before/after JSON).
- All labels come from the translation layer and the industry template.
