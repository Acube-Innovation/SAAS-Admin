# Super Admin Console — Specification

> **Derived document.** Written from the *System Architecture* doc, the *Functional Spec (Pilot MVP)* and the
> *Claude Code Build Prompts* (27 Oct 2026). Replace it with the official Super Admin Console guide when that is
> available. Where the build prompts state an exact rule, this spec uses it word for word.

The super admin console (`admin.<domain>`) is used by our own team to onboard clients (tenants), set up
industries and message templates, watch sending health and give audited support. It calls only the Django API
under `/api/admin/`.

---

## 1. Users and roles

| Group | Role (`role` value) | Who |
| --- | --- | --- |
| Platform | `super_admin` | Our team's administrators |
| Platform | `support` | Our support staff |
| Tenant | `owner` | Client owner (one or more per tenant) |
| Tenant | `manager` | Client manager |
| Tenant | `office_staff` | Client office staff |

Platform roles live on `User.platform_role`. Tenant roles live on `Membership.role`. A user can hold memberships in
more than one tenant.

2FA (TOTP) is mandatory for `super_admin`, `support` and `owner`. It is optional for the other roles.

### 1.1 Platform role split

| Action | super_admin | support |
| --- | --- | --- |
| View dashboard, clients, client detail, audit log | Yes | Yes |
| Create client, edit client details and settings | Yes | No |
| Suspend / activate client | Yes | No |
| Open a client as support (reason, 60 min) | Yes | Yes |
| Resend owner/user invites | Yes | Yes |
| Create / edit industry templates | Yes | No |
| Draft and edit WhatsApp template versions | Yes | Yes |
| Send a template version to the client for approval | Yes | Yes |
| Manual client sign-off fallback (with evidence) | Yes | No |
| Submit a template to Meta | Yes | No |
| Make a template version live / pause / disable | Yes | No |
| Change requests: view, assign, start work, reply, link draft | Yes | Yes |
| Change requests: decline with reason | Yes | Yes |
| Plans, price list, per-client price overrides, plan changes | Yes | No |
| Export audit log CSV | Yes | Yes |

---

## 2. Screens and fields

Field names below are the exact snake_case names used in the DB and API.

### 2.1 Login

| Field | Rules |
| --- | --- |
| `email` | Required, valid email, case-insensitive, unique |
| `password` | Required, **minimum 12 characters**. Validated with Django's validators (similarity, common, numeric) |
| `otp_code` | 6 digits. Asked after the password step when the user has a confirmed TOTP device |

- **Lock-out:** 5 consecutive failed password attempts lock the account for **15 minutes** (`locked_until`).
  Login attempts during a lock are rejected with the same generic message and not counted again. A successful
  login resets `failed_login_count`.
- Session cookie auth with CSRF (`SameSite=Lax`, `HttpOnly`, `Secure` outside local).
- Users who must have 2FA but have no confirmed device are sent to **2FA set-up** (QR code + confirm code) before
  they can reach any page.
- Admin API access needs `platform_role` set **and** an OTP-verified session.
- Error messages never reveal whether the email exists.

### 2.2 Dashboard

Tiles (today, Asia/Kolkata). Message tiles read **0 until Phase 2**, when the reminder engine exists:

| Tile | Source |
| --- | --- |
| Active clients / suspended clients | Tenant counts by `status` |
| Messages sent / delivered / failed / replied today | Attempts and responses (Phase 2) |
| WhatsApp number quality rating (GREEN / YELLOW / RED) | Provider status. **Red alert** when Meta lowers it |
| Failed webhooks (24 h) | Webhook ingest errors (Phase 2) |
| Templates awaiting action | WhatsApp template versions in `signed_off`, `sent_back`, `rejected` |
| Open change requests | Change requests not Done or Declined |
| Active support sessions | Support sessions not ended and not expired |

### 2.3 Clients list

Columns: `name`, `industry_template`, `status` (badge), `city`, `state`, owner email, `created_at`.
Search by name, legal name, GSTIN or owner email. Filter by `status` and `industry_template`. Sort by name or
created date. 25 rows per page. "New client" button (super_admin only).

### 2.4 Create client form

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `name` | text | Yes | Display name, 2–100 characters |
| `legal_name` | text | Yes | 2–200 characters |
| `gstin` | text | No | 15 characters, uppercased, format `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$`. The first 2 digits must match the state's GST code. **Unique** across tenants when present |
| `address_line1` | text | Yes | Up to 200 characters |
| `address_line2` | text | No | Up to 200 characters |
| `city` | text | Yes | Up to 100 characters |
| `state` | choice | Yes | Indian states and union territories list (with GST state codes), stored as the 2-letter code, e.g. `KL` |
| `pin_code` | text | Yes | 6 digits, first digit 1–9 (`^[1-9][0-9]{5}$`) |
| `contact_name` | text | Yes | Up to 100 characters |
| `contact_phone` | phone | Yes | Indian mobile, stored E.164 `+91XXXXXXXXXX`; 10 digits starting 6–9 |
| `contact_email` | email | Yes | Valid email |
| `industry_template` | choice | Yes | An active industry template (pilot: `gas_agency`). Added to the API with apps/industry in Step 6 |
| `default_language` | choice | Yes | `ml`, `en`, `hi`, `ta`. Default `ml` |
| `daily_send_cap` | integer | Yes | **Default 5000**, 1–100000 |
| `owner_name` | text | Yes | Up to 100 characters |
| `owner_email` | email | Yes | Becomes the owner's login (existing users get a new membership) |
| `owner_phone` | phone | No | Same phone rules |

### 2.5 Client detail

Header: name, status badge, industry, buttons **Suspend / Activate** (super_admin) and **Open as support**.
A suspended client shows a **read-only banner** with the suspension reason and date.

| Tab | Content |
| --- | --- |
| Details | All 2.4 fields (edit: super_admin) |
| Users | Memberships: name, email, `role`, `status` (invited / active / deactivated), last login, 2FA on/off. **Resend invite** for invited users |
| Settings | `daily_send_cap`, `default_language`, `timezone` (default `Asia/Kolkata`), reminder rule summary (read-only, comes from the industry template defaults) |
| Templates | WhatsApp template versions linked to this client, with status |
| Activity | This tenant's audit log entries, newest first |
| Billing | Plan, pending plan change, price overrides, read-only wallet view (added in Step A2, see 2.11 and 3.8) |

### 2.6 Industry template editor

| Field | Rules |
| --- | --- |
| `key` | slug `^[a-z][a-z0-9_]{2,49}$`, unique, **immutable** after create (e.g. `gas_agency`) |
| `name` | 2–100 characters |
| `description` | Optional, up to 500 characters |
| `is_active` | Inactive templates can't be picked for new clients |
| Schedule types | `key`, `name` (e.g. "Cylinder delivery"), linked default reminder rule and question flow |
| Field definitions (per entity `subscriber` or `schedule`) | `key` (snake_case `^[a-z][a-z0-9_]{1,49}$`), `label` (1–100), `field_type` (`text`, `number`, `date`, `phone`, `choice`, `boolean`), `required`, `options` (choice fields: at least one, unique), `max_length` (1–1000), `is_active`, order = position in the list. **Core** fields (`is_core`) map to model columns (e.g. `customer_name`, `external_ref`, `booking_reference`, `scheduled_date`): only their label can change and they can't be removed. Other fields are **custom** and stored in `custom_fields` JSONB |
| Default reminder rule | `send_time`, `quiet_start`, `quiet_end` (HH:MM 24-hour), `days_before` (0–7, default 1), `wait_minutes` (30–1440), `delivery_weekdays` (0 = Monday … 6, at least one), `channel_order` (`whatsapp`, `voice`) |
| Default question flow | 1–3 answers, each `key`, `label` (≤ 25 characters: a WhatsApp button), `outcome` (`confirmed`, `not_available`, `reschedule`), optional `pick_next_delivery_days` (1–10) |
| Choice lists | The options of custom choice fields, e.g. cylinder types. Copied onto each client, which can edit its own list |

The editor saves the **whole definition** with `PUT` (the read shape of `GET` can be sent back as is).

Saving publishes a **new version** (see Workflow 3.5).

**gas_agency seed:** schedule type *Cylinder delivery*. Subscriber fields: customer name, mobile number, alternate
number, consumer number, preferred language, address, area, WhatsApp consent. Booking fields: booking reference,
booking date, planned delivery date, cylinder type, quantity, remarks. Cylinder types: 14.2 kg domestic, 5 kg,
10 kg composite, 19 kg commercial, 47.5 kg commercial. Default rule: 10:00 send, 240-minute wait, quiet hours
21:00–08:00. Flow: *Yes, available* → confirmed, *Not available* → not_available, *Change date* → reschedule
(pick list of the next 5 delivery days).

### 2.7 WhatsApp template editor

| Field | Rules |
| --- | --- |
| `name` | `^[a-z0-9_]{1,512}$` (Meta format, lowercase and underscores), unique per tenant + language |
| `language` | `ml`, `en`, `hi`, `ta` |
| `category` | `utility` (only utility in the pilot) |
| `schedule_type` | The schedule type whose fields can be used as variables |
| `header_text` | Optional, up to 60 characters, at most 1 variable |
| `body` | Required, **up to 1,024 characters**. Variables `{{field_key}}` limited to the schedule type's fields, the template's subscriber fields (e.g. `customer_name`) and `agency_name` (the variables picker lists them: `GET /api/admin/whatsapp-templates/variables?schedule_type=`). Stored by name; the Meta adapter converts to positional `{{1}}` at submission |
| `footer` | Optional, **up to 60 characters, no variables** |
| `buttons` | Up to 3 quick replies, each **up to 25 characters** and unique, each mapped to an outcome (`confirmed`, `not_available`, `reschedule`) |
| `sample_values` | **Required for every variable** used in header/body |
| `signoff_evidence` | File (PDF/PNG/JPG; the content must match the type), **up to 5 MB**, uploaded to S3 under `tenants/<tenant>/whatsapp-signoff/<version>/`. Required for manual sign-off. Downloaded through a signed link valid for 5 minutes |

The editor shows live character counters, a variables picker, a preview per language, the Workflow 3.4 status at
the top, version history and the rejection reason when rejected.

### 2.8 Audit log

Columns: `created_at`, actor (user email, or "system"), `tenant`, `action`, `object_type`, `object_id`,
`support_session` (when the action was taken under support access), `ip_address`.
Filters: tenant, actor, action, object type, date range. Row detail shows a **before/after diff**.
**CSV export** of the filtered rows. The export itself writes an audit entry (`action = export`).
Read-only: entries are never edited or deleted.

### 2.9 Change requests

Requests raised by clients from the portal about message content (template wording, new language, voice script).

| Field | Rules |
| --- | --- |
| `tenant`, `requested_by` | From the portal |
| `request_type` | `new_template`, `edit_template`, `new_language`, `voice_script`, `other` |
| `subject` | 5–150 characters |
| `description` | Up to 2,000 characters |
| `status` | `open` → `in_progress` → `done` / `declined` |
| `assignee` | Platform user |
| `linked_version` | A draft template version. The request moves to **Done automatically when that version goes live** |
| `decline_reason` | Required to decline |
| Comments | Thread between client and platform staff |

Queue filters: status, tenant, assignee, type. Actions: assign, start work, reply, decline with reason, link draft.

### 2.10 Voice script editor (Phase 3)

Per language: `greeting`, `question`, answers mapped to outcomes, `voice`, `max_retries` (default 2),
`retry_gap_minutes` (default 60). Audio preview through the voice provider interface. Lifecycle as in Workflow 3.7.
Calls respect quiet hours, delivery days and opt-outs.

### 2.11 Plans and price list

| Object | Fields |
| --- | --- |
| Plan | `code`, `name`, `monthly_fee_paise`, `max_active_subscribers`, `features` (voice_ai, public_api, staff_app), `is_active` |
| Price list | `channel` (`whatsapp_utility`, `whatsapp_service`, `voice_minute`), `price_paise`, `effective_from` |
| Price override (per client) | `channel`, `price_paise`, `starts_on`, `ends_on`. **No backdating** (`starts_on` ≥ today) and **no overlaps** for the same channel |
| Subscription | `plan`, `status`, `current_period_start`, `next_billing_date`, `pending_plan` |

All money is stored in **paise** (integer).

---

## 3. Workflows

### 3.1 Onboard a client (create client)

One database transaction:
1. Validate the 2.4 form (GSTIN format, state code match, uniqueness, PIN, phones).
2. Create the `Tenant` with `status = active`. Copy the industry template's current version defaults (settings,
   reminder rule, choice lists) onto the tenant.
3. Create or find the `User` for `owner_email`. Create the `Membership(role = owner, status = invited)`.
4. Create an `Invite`: a token `<tenant_id>.<random secret>`, of which only the **SHA-256 hash** is stored. The
   tenant part lets acceptance set the tenant context; the secret is what makes it unguessable. **Single use**,
   **expires after 72 hours**.
5. Write audit entries (tenant created, membership created, invite created).
6. After commit, queue the invite email (SES) with the link `https://app.<domain>/invite/<token>`.

Accepting an invite: the token is valid, unused and not expired. A new user sets a password (2.1 rules); an existing
user with a password confirms it. Then `Invite.used_at` is set, the membership becomes `active` and the user is
signed in. Users who need 2FA (owners) can't use anything until they finish 2FA set-up, which comes next.
**Resend:** invalidates any earlier unused invite for that membership, creates a new token with a new 72-hour
expiry, and is audited.

### 3.2 Suspend and activate

- **Suspend** (super_admin): `reason` required (5–500 characters). Sets `status = suspended`, `suspended_at`,
  `suspension_reason`. From then on `tenant.can_send()` returns False, so nothing is sent. Portal users get
  **read-only** access: GETs work, and writes return `403` with code `tenant_suspended`. Audited.
- **Activate** (super_admin): `reason` required. Sets `status = active`. Reminders that were held during the
  suspension and whose send time has passed are marked **Skipped** (hook `on_tenant_reactivated(tenant)` for the
  Phase 2 reminder engine). Audited.

### 3.3 Support access

- Platform staff click **Open as support** and give a `reason` (5–500 characters). This creates a
  `SupportSession(staff_user, tenant, reason, started_at, expires_at = started_at + 60 min)`.
- While the session is active, the staff user acts in the tenant's context: **read-only**, matching the portal's
  "View" column for platform support (writes get `403 support_read_only`). Every audit entry written carries
  `support_session`. The UI shows a support-mode banner with the time left. Opening a new session ends the
  user's previous one (`end_reason = replaced`).
- A session ends when it is ended manually or when the expiry task (Celery Beat, every minute) sets `ended_at`
  on expired sessions (audited with no actor: the system). A request with an expired session gets
  `403 support_session_expired`.
- Start and end are audited.

### 3.4 WhatsApp template lifecycle

States: `draft`, `sent_to_client`, `signed_off`, `sent_back`, `submitted`, `approved`, `rejected`, `paused`,
`disabled`. "Live" is a flag (`is_live`) on one approved version per template.

| From | Action | To | Who |
| --- | --- | --- | --- |
| draft | edit | draft | super_admin, support |
| draft | send to client | sent_to_client | super_admin, support |
| sent_to_client | client approves (portal, Step 18) | signed_off | client owner / manager |
| sent_to_client | client sends back with comment (portal) | sent_back | client owner / manager |
| sent_to_client | manual sign-off with evidence file | signed_off | **super_admin only** |
| sent_back | edit | draft | super_admin, support |
| signed_off | submit to Meta | submitted | super_admin |
| submitted | provider reports approved | approved | system |
| submitted | provider reports rejected (+ reason) | rejected | system |
| approved | make live | approved + `is_live` (previous live version stays approved, `is_live = false`) | super_admin |
| approved / live | provider pauses | paused | system |
| paused | provider re-enables | approved | system |
| any except disabled | disable | disabled | super_admin |
| approved / live / rejected | new version from live | new `draft` version (version + 1) | super_admin, support |

Every other transition is blocked (`409 invalid_transition`). Approved versions are immutable. Only one version per
template can be live (database constraint). A new version can't start while another is in progress (draft, sent to
client, signed off, sent back or submitted): `409 open_version_exists`. Provider statuses are pulled by
`sync-status` and by Celery Beat every 10 minutes (Phase 2 adds Meta's status webhook). Each transition
writes an audit entry and a `TemplateStatusEvent`. The `WhatsAppProvider` interface has `submit_template()` and
`get_template_status()`. `StubProvider` is used in tests and local, `MetaCloudProvider` comes in Phase 2.

### 3.5 Industry template change

- Every save creates a new `IndustryTemplateVersion` (version + 1). The change is audited with before/after.
- **New optional field:** applies to all clients that use the template.
- **Removed field:** switched off (`is_active = false`) and kept, so existing data stays readable.
- **Default changes** (reminder rule, choice lists): apply to **new clients only**. Existing clients keep their copy.
- **New required field:** blocked (`409 required_field_in_use`) once any client uses the template.
- A field's `key` and `field_type` can't change once the template is in use. (A key is the field's identity:
  a different key is a new field.) Making a field required, or switching a required field back on, counts as a
  new required field.
- Core fields: only the label can change, at any time.
- "In use" = at least one client has this template. When a client is created it gets a copy of the template's
  defaults (reminder rule, question flow, choice lists) in `Tenant.settings.industry`, plus
  `industry_template_version`. Fields are always read live from the template, so new optional fields apply to all
  clients.

### 3.6 Change request

1. A client raises a request in the portal: `status = open`, and the admin queue is notified.
2. Platform staff assign it and click **Start work**: `in_progress`.
3. Staff and client exchange comments.
4. Staff link a draft template version, then send it to the client for approval (3.4).
5. When the linked version goes live, the request becomes **done** automatically.
6. Or staff **decline** with a reason: `declined`.
Every step is audited.

### 3.7 Voice script lifecycle (Phase 3)

`draft` → `sent_to_client` → `signed_off` → `live`. Sent back and manual sign-off rules match 3.4. Only super_admin
can make a script live.

### 3.8 Plan change

- Super admin picks a new plan. It is saved as `pending_plan` with `effective_on = next_billing_date`.
- A scheduled task applies it on that date, issues the invoice and sends a client notice.
- A pending change can be cancelled before it applies. Every step is audited.

---

## 4. Non-functional requirements

| Area | Requirement |
| --- | --- |
| Data separation | Every tenant table has `tenant_id` + PostgreSQL RLS. Automated tests must fail any cross-tenant read |
| Audit | Every create, update, delete, status change, export and support login is logged with actor, time, before, after |
| Security | HTTPS only, session cookies + CSRF, 2FA for platform staff and owners, secrets from env / Secrets Manager |
| Speed | Pages within 2 s at 50,000 subscribers per tenant |
| Languages | All UI text through the translation layer, English first |
| Hosting | AWS ap-south-1 (Mumbai) |
| Time | Stored in UTC, shown in `Asia/Kolkata` |

---

## 5. Repository structure

Two repositories: **SAAS-Admin** (below: backend for both apps, admin console, infrastructure) and
**SAAS-Client** (the client portal: `apps/portal` plus its own copy of the shared packages).

```
/
├─ CLAUDE.md                     project rules
├─ README.md                     run instructions
├─ docker-compose.yml            db, redis, api, worker (local)
├─ .env.example                  every env var, no real secrets
├─ .github/workflows/ci.yml      lint + tests for backend and frontend
├─ docs/
│  ├─ admin-console-spec.md
│  ├─ client-portal-spec.md
│  └─ screenshots/
├─ backend/                      Django project (modular monolith)
│  ├─ Dockerfile
│  ├─ manage.py
│  ├─ pyproject.toml             ruff + pytest config
│  ├─ requirements/              base.txt, local.txt, production.txt
│  ├─ config/
│  │  ├─ settings/               base.py, local.py, staging.py, production.py, test.py
│  │  ├─ urls.py  wsgi.py  asgi.py
│  │  └─ celery.py
│  ├─ apps/
│  │  ├─ core/                   health, tenancy base (TenantScopedModel, RLS helpers, middleware, base task)
│  │  ├─ accounts/               users, memberships, login, 2FA, invites          (Step 3)
│  │  ├─ audit/                  AuditLog                                          (Step 4)
│  │  ├─ platform_admin/         tenants API, suspend, support sessions            (Step 5)
│  │  ├─ industry/               industry templates                                (Step 6)
│  │  ├─ messaging_templates/    WhatsApp templates + provider interface           (Step 7)
│  │  ├─ subscribers/  schedules/  jobs/  content_view/  change_requests/
│  │  ├─ billing/  integrations/                                                   (client portal steps)
│  └─ tests/
├─ frontend/                     Turborepo + pnpm workspace
│  ├─ package.json  pnpm-workspace.yaml  turbo.json
│  ├─ apps/
│  │  └─ admin/                  Next.js super admin console (port 3000)
│  │                             (client portal: SAAS-Client repo, port 3001)
│  └─ packages/
│     ├─ ui/                     shared components
│     ├─ api-client/             typed client generated from /api/schema
│     └─ config/                 shared tsconfig, eslint, tailwind preset
└─ infra/terraform/              AWS staging/production                        (Step 12)
```

---

## 6. Data model

All primary keys are UUIDs. Every model has `created_at` and `updated_at`. **Tenant-scoped** tables carry
`tenant_id` (FK, indexed first) and an RLS policy.

| Model | App | Tenant-scoped | Key fields |
| --- | --- | --- | --- |
| Tenant | core | — (is the tenant) | `name`, `legal_name`, `gstin` (unique, nullable), `address_line1`, `address_line2`, `city`, `state`, `pin_code`, `contact_name`, `contact_phone`, `contact_email`, `industry_template` (FK, required on create, can't change afterwards), `industry_template_version` (int, the version whose defaults were copied), `status` (`active`, `suspended`), `suspended_at`, `suspension_reason`, `default_language`, `timezone`, `daily_send_cap` (default 5000), `settings` (JSONB) |
| User | accounts | No | `email` (unique, ci), `full_name`, `phone`, `platform_role` (`super_admin`, `support`, or blank), `is_active`, `failed_login_count`, `locked_until`, `last_login` |
| Membership | accounts | Yes (plus a read rule: users can read their own memberships in any tenant) | `user`, `tenant`, `role` (`owner`, `manager`, `office_staff`), `status` (`invited`, `active`, `deactivated`); unique (`tenant`, `user`) |
| Invite | accounts | Yes | `membership`, `token_hash` (SHA-256), `expires_at`, `used_at`, `revoked_at`, `created_by` |
| TOTP device | accounts | No | django-otp `TOTPDevice` (`confirmed`) |
| AuditLog | audit | Nullable tenant (platform actions have none); RLS: own tenant, or all for 2FA-verified platform staff | `actor` (FK User, null = system), `actor_email`, `tenant`, `support_session` (stored as `support_session_id`; FK from Step 5), `action` (`create`, `update`, `delete`, `status_change`, `export`, `login`, `login_failed`, `account_locked`, `logout`, `otp_*`, `invite_accepted`, `support_start`, `support_end`, …), `object_type`, `object_id`, `before` (JSONB), `after` (JSONB), `ip_address`, `user_agent`. **Insert-only**: Django blocks save/delete/queryset update, a database trigger rejects UPDATE/DELETE for every role, and `app_user` has no UPDATE/DELETE/TRUNCATE. Secrets (`password`, `token_hash`, keys) are masked |
| SupportSession | platform_admin | No | `staff_user`, `tenant`, `reason`, `started_at`, `expires_at`, `ended_at` |
| IndustryTemplate | industry | No | `key` (immutable), `name`, `description`, `is_active`, `current_version` |
| IndustryTemplateVersion | industry | No | `template`, `version`, `definition` (JSONB: schedule types, fields, choice lists, default rule, default flow), `created_by` |
| ScheduleType | industry | No | `template`, `key`, `name`, `is_active`, `default_reminder_rule` (JSON), `default_question_flow` (JSON) |
| FieldDefinition | industry | No | `template`, `schedule_type` (schedule fields), `entity` (`subscriber`, `schedule`), `key`, `label`, `field_type`, `required`, `is_core`, `options`, `max_length`, `is_active`, `order`. Never deleted: removed fields are switched off |
| WhatsAppTemplate | messaging_templates | Yes | `tenant`, `name`, `language`, `category`, `schedule_type` |
| WhatsAppTemplateVersion | messaging_templates | Yes | `template`, `version`, `status`, `is_live`, `header_text`, `body`, `footer`, `buttons` (JSONB), `sample_values` (JSONB), `provider_template_id`, `rejection_reason`, `signoff_evidence` (S3 key), `signed_off_by`, `signed_off_at`, `signoff_method` (`client`, `manual`) |
| TemplateStatusEvent | messaging_templates | Yes | `version`, `from_status`, `to_status`, `actor`, `note` |
| ChangeRequest / ChangeRequestComment | change_requests | Yes | see 2.9 |
| VoiceScript / VoiceScriptVersion | messaging_templates | Yes | see 2.10 |
| Plan, PriceList, PriceOverride, Subscription | billing | Plan/PriceList: No; others: Yes | see 2.11 |

---

## 7. Admin API endpoints

All under `/api/admin/`. They need an authenticated, OTP-verified user with `platform_role`. Write endpoints marked
**SA** need `super_admin`. The schema is at `/api/schema` and the docs at `/api/docs`.

| Method | Path | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/health` | Liveness: DB + Redis | public |
| GET | `/api/auth/csrf` | Sets the `csrftoken` cookie (sent back as `X-CSRFToken`) | public |
| POST | `/api/auth/login` | Email + password | public |
| POST | `/api/auth/otp/verify` | TOTP code | logged in |
| POST | `/api/auth/otp/setup` · `/confirm` | 2FA set-up | logged in |
| POST | `/api/auth/logout` | | logged in |
| GET | `/api/auth/me` | Current user, roles, 2FA state, memberships | logged in |
| POST | `/api/auth/tenant` | Choose the active tenant (users with several memberships) | logged in + 2FA |
| GET/POST | `/api/auth/invites/<token>` · `/accept` | Invite check and accept | public |
| GET | `/api/admin/dashboard` | Tiles (2.2) | staff |
| GET | `/api/admin/tenants` | List, search, filter | staff |
| POST | `/api/admin/tenants` | Create client (3.1) | SA |
| GET | `/api/admin/tenants/{id}` | Detail | staff |
| PATCH | `/api/admin/tenants/{id}` | Edit details / settings | SA |
| POST | `/api/admin/tenants/{id}/suspend` | Suspend (reason) | SA |
| POST | `/api/admin/tenants/{id}/activate` | Activate (reason) | SA |
| GET | `/api/admin/tenants/{id}/users` | Memberships | staff |
| POST | `/api/admin/tenants/{id}/users/{membership_id}/resend-invite` | Resend invite | staff |
| GET | `/api/admin/tenants/{id}/activity` | Tenant audit entries | staff |
| POST | `/api/admin/tenants/{id}/support-sessions` | Start support (reason) | staff |
| GET | `/api/admin/support-sessions/current` | The user's active session (support-mode banner: tenant, `seconds_left`); 204 if none | staff |
| POST | `/api/admin/support-sessions/{id}/end` | End support | staff |
| GET/POST | `/api/admin/industry-templates` | List / create | staff / SA |
| GET/PUT | `/api/admin/industry-templates/{id}` | Detail / save new version (3.5) | staff / SA |
| GET | `/api/admin/industry-templates/{id}/versions` | Version history | staff |
| GET/POST | `/api/admin/whatsapp-templates` | List / create | staff |
| GET/PATCH | `/api/admin/whatsapp-templates/{id}/versions/{vid}` | Detail / edit draft | staff |
| POST | `…/versions/{vid}/send-to-client` · `/manual-signoff` (multipart) · `/submit` · `/make-live` · `/disable` · `/new-version` · `/sync-status` | 3.4 actions (manual sign-off, submit, make live, disable: super admin) | per 3.4 |
| GET | `/api/admin/whatsapp-templates/variables?schedule_type=` · `…/versions/{vid}/evidence` | Variables picker · signed evidence link (5 min) | staff |
| GET/PATCH | `/api/admin/change-requests[/{id}]` | Queue / detail / assign / status | staff |
| POST | `/api/admin/change-requests/{id}/comments` · `/decline` · `/link-version` | | staff |
| GET | `/api/admin/audit-logs` · `/audit-logs/export` | Audit list / CSV (audited) | staff |
| GET/POST/PATCH | `/api/admin/plans` · `/price-list` · `/tenants/{id}/subscription` · `/tenants/{id}/price-overrides` | Billing (2.11, 3.8) | SA |

Errors use `{"code": "...", "detail": "...", "fields": {...}}`.

---

## 8. Tenancy and Row-Level Security

1. `apps/core` provides `TenantScopedModel` (abstract: UUID `id`, `tenant` FK, timestamps). Every tenant table
   inherits it. `save()` fills `tenant` from the current tenant context.
2. The migration operation `apps.core.rls.EnableRLS("<model>")` runs `ALTER TABLE … ENABLE ROW LEVEL SECURITY`,
   `ALTER TABLE … FORCE ROW LEVEL SECURITY` and
   `CREATE POLICY tenant_isolation ON … USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
   WITH CHECK (same)`. `NULLIF` makes "no tenant set" match no rows instead of raising an error. A test fails for
   any `TenantScopedModel` whose table lacks RLS, FORCE or the policy.
3. Database roles (created by `infra/docker/postgres/init-roles.sh`):
   - `postgres`: superuser, used only to bootstrap the roles. Django never connects as it.
   - `app_owner`: owns the database, schema and tables; runs migrations (`DJANGO_DB_ROLE=owner`) and tests.
     `NOSUPERUSER NOBYPASSRLS`, so FORCE RLS applies to it too.
   - `app_user`: the API and workers (`DJANGO_DB_ROLE=app`, the default). `NOSUPERUSER NOBYPASSRLS`, never a
     table owner, so it can't alter tables, drop policies or switch `row_security` off. It gets DML on all tables
     after each migrate, and read-only access to `django_migrations`.
   - Cross-tenant platform reporting (later) gets its own role with `BYPASSRLS`, used only by those jobs.
4. `TenantMiddleware` resolves the tenant with `settings.TENANCY_RESOLVER` (Step 3: membership or active support
   session) and runs the rest of the request inside `tenant_context(tenant)`. That is one transaction in which
   `set_config('app.tenant_id', <uuid>, true)` is set; it is transaction-local, so it can't leak to the next request
   on a pooled connection. The Celery base task `TenantTask` does the same around each task: it requires a
   `tenant_id` kwarg, which is filled in automatically when the task is queued inside a tenant context.
5. With no tenant set, tenant tables return no rows and reject writes.
5a. `tenant_context(t)` always switches platform access off: inside it even platform staff see exactly tenant `t`.
   Admin code still filters by tenant explicitly when it reads tenant tables.
5b. For 2FA-verified platform staff the middleware also sets `app.platform_access = 'on'`. Only policies that opt in
   (`EnableRLS(..., extra_using=PLATFORM_ACCESS)`: audit log, memberships, support sessions) use it, to let the admin console read
   every tenant's rows. Tables with platform-level rows use `nullable_tenant=True` (writes need
   `tenant_id IS NOT DISTINCT FROM app.tenant_id`).
6. Every tenant index starts with `tenant_id`.
7. Tests create two tenants and prove that a query under tenant A, **including raw SQL**, never returns or
   modifies tenant B's rows. They also connect as `app_user` to prove RLS is enforced for that role and can't be
   switched off.

### 8.1 Admin additions built alongside the client portal

| Portal step | Admin addition |
| --- | --- |
| 18 / A1 | Change requests queue + detail (2.9, 3.6). **Send to client for approval** action on WhatsApp template versions |
| 21 / A2 | Plans, price list, subscription management, Client detail Billing tab (2.11, 3.8) |
| A3 | Voice script editor (2.10, 3.7) |
