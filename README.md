# Schedule Confirmation SaaS

Multi-tenant SaaS that reminds our clients' customers (gas agencies first) about upcoming schedules over
WhatsApp, and later AI voice, and records their answers.

This repository (SAAS-Admin) holds the backend API, the super admin console and the infrastructure.
The client portal frontend is in [SAAS-Client](https://github.com/Acube-Innovation/SAAS-Client).

- Project rules: [CLAUDE.md](CLAUDE.md)
- Specs: [docs/admin-console-spec.md](docs/admin-console-spec.md), [docs/client-portal-spec.md](docs/client-portal-spec.md)

## Layout

```
backend/            Django + DRF API for both apps, Celery worker and beat (modular monolith)
frontend/           Turborepo + pnpm workspace
  apps/admin        Super admin console (Next.js, port 3000)
  packages/ui       Shared UI components + translation helper (also in SAAS-Client)
  packages/api-client  Typed API client
  packages/config   Shared tsconfig, ESLint and Tailwind theme
infra/terraform/    AWS ap-south-1 staging (ECS Fargate, RDS, ElastiCache, S3, SES); see its README
docs/               Specs and screenshots
```

## Prerequisites

- Docker Engine with the Compose plugin
- Node.js 22 LTS and pnpm 10 (`npm i -g pnpm@10`)
- Python 3.12 (only to run backend tooling outside Docker)

## Run the backend (Docker Compose)

```bash
cp .env.example .env              # local-only values, never commit .env
docker compose up --build         # db (PostgreSQL 16), redis, storage (S3), api, worker, beat
curl -i http://localhost:8000/api/health
# HTTP/1.1 200 OK  {"status":"ok","database":"ok","redis":"ok"}
```

| Service | What it runs | Port |
| --- | --- | --- |
| `db` | PostgreSQL 16 | 5432 |
| `redis` | Redis 7 (Celery broker, cache) | 6379 |
| `api` | Django (migrations, then the dev server with auto-reload) | 8000 |
| `worker` | Celery worker | — |
| `beat` | Celery Beat scheduler (e.g. ends expired support sessions every minute) | — |
| `storage` | SeaweedFS S3 API: local stand-in for AWS S3 (sign-off evidence, later imports and recordings) | 8333 |

If a port is already used on your machine (for example a local Redis on 6379), set `POSTGRES_HOST_PORT`,
`REDIS_HOST_PORT` or `API_HOST_PORT` in `.env`. Also update `POSTGRES_PORT` / `REDIS_URL` there for tools run on
the host.

Create the first super admin (they set up 2FA with an authenticator app at first sign-in):

```bash
docker compose exec api python manage.py createplatformuser --email you@example.com --role super_admin
```

Demo data for the client portal: a gas agency with areas, staff, subscribers, bookings, WhatsApp messages, voice
calls and outcomes (owner, manager and office staff users; the password is printed once):

```bash
docker compose exec api python manage.py seed_demo                  # --subscribers 2000, --name "Another Agency"
```

The API creates the S3 bucket on start-up (`manage.py ensure_storage_bucket`). WhatsApp templates use a stub provider
(no calls to Meta) until Phase 2. In the admin console's template editor, super admins get a development-only
**Simulate** panel to play Meta (approve / reject / pause) and the client (approve / send back); it only exists with
the stub provider and `DEBUG` on.

Emails (such as invites) print to the `worker` logs locally: `docker compose logs -f worker`.

The API docs are at http://localhost:8000/api/docs, and the OpenAPI schema is at http://localhost:8000/api/schema.
The production image (`backend/Dockerfile`) runs Gunicorn.

### Database roles and Row-Level Security

Every tenant table is protected by PostgreSQL Row-Level Security. Django never connects as the superuser:

| Role | Used by | Notes |
| --- | --- | --- |
| `postgres` | Postgres container bootstrap only | Creates the two roles below (`infra/docker/postgres/init-roles.sh`) |
| `app_owner` | Migrations (`DJANGO_DB_ROLE=owner`) and tests | Owns the tables; not superuser, no BYPASSRLS |
| `app_user` | API and Celery workers (default) | DML only; can't bypass or disable RLS |

The role script runs only when the Postgres volume is empty. If you started the stack before Step 2, reset the local
database once with `docker compose down -v && docker compose up -d --build`. This deletes local data.

## Run the frontend

```bash
cd frontend
pnpm install
pnpm dev                          # admin console on http://localhost:3000 (portal: see SAAS-Client)
```

Sign in at http://localhost:3000/login with a platform user (see `createplatformuser` above). The first sign-in
shows a QR code for an authenticator app (2FA is required for platform staff).

The admin app proxies `/api/*` to `API_INTERNAL_URL` or `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).

## Typed API client

`frontend/packages/api-client` is generated from the backend's OpenAPI schema. After changing an endpoint or serializer:

```bash
scripts/generate-api-client.sh    # writes openapi.yaml and src/schema.d.ts; commit both
```

CI fails if the committed client doesn't match the backend.

## End-to-end tests (Playwright)

`frontend/apps/admin/e2e` covers Workflows 3.1 (onboard a client), 3.2 (suspend / reactivate), 3.3 (support access),
every Workflow 3.4 WhatsApp template path and the industry template editor, in a real browser against the running
API. With `docker compose up` running:

```bash
cd frontend/apps/admin
pnpm exec playwright install chromium   # once (or set E2E_CHROME_PATH=/usr/bin/google-chrome)
pnpm e2e
```

Each run creates its own super admin and support user (via `createplatformuser`) and signs them in with real 2FA.
The tests create clients in your local database. CI runs the same suite against a fresh database (`e2e` job).

## Tests and lint

Backend (with `db` and `redis` running from Docker Compose):

```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements/local.txt
set -a; . ../.env; set +a
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
```

Or inside the container: `docker compose exec api pytest`.

Frontend:

```bash
cd frontend
pnpm check                        # turbo: lint, typecheck, test, build
```

CI (`.github/workflows/ci.yml`) runs the same commands on every pull request and on pushes to `main`.
It also checks the Terraform (`fmt`, `validate`, `tflint`, `terraform test` with a mocked AWS provider).
On `main`, once every job passes, it builds the images, pushes them to ECR, runs migrations and deploys
to staging (`scripts/deploy-ecs.sh`). This is enabled by the `STAGING_DEPLOY` variable. First-time AWS
set-up: [infra/terraform/README.md](infra/terraform/README.md).

## Settings

`backend/config/settings/` holds `base`, `local`, `staging`, `production` and `test`. Pick one with
`DJANGO_SETTINGS_MODULE`. Every secret comes from environment variables. See `.env.example` for the full list.
