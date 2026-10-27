# Schedule Confirmation SaaS

Multi-tenant SaaS that reminds our clients' customers (gas agencies first) about upcoming schedules over
WhatsApp, and later AI voice, and records their answers.

- Project rules: [CLAUDE.md](CLAUDE.md)
- Specs: [docs/admin-console-spec.md](docs/admin-console-spec.md), [docs/client-portal-spec.md](docs/client-portal-spec.md)

## Layout

```
backend/            Django + DRF API, Celery worker (modular monolith)
frontend/           Turborepo + pnpm workspace
  apps/admin        Super admin console (Next.js, port 3000)
  apps/portal       Client portal (Next.js, port 3001)
  packages/ui       Shared UI components + translation helper
  packages/api-client  Typed API client
  packages/config   Shared tsconfig, ESLint and Tailwind theme
infra/terraform/    AWS infrastructure (from Step 12)
docs/               Specs and screenshots
```

## Prerequisites

- Docker Engine with the Compose plugin
- Node.js 22 LTS and pnpm 10 (`npm i -g pnpm@10`)
- Python 3.12 (only to run backend tooling outside Docker)

## Run the backend (Docker Compose)

```bash
cp .env.example .env              # local-only values, never commit .env
docker compose up --build         # db (PostgreSQL 16), redis, api, worker
curl -i http://localhost:8000/api/health
# HTTP/1.1 200 OK  {"status":"ok","database":"ok","redis":"ok"}
```

| Service | What it runs | Port |
| --- | --- | --- |
| `db` | PostgreSQL 16 | 5432 |
| `redis` | Redis 7 (Celery broker, cache) | 6379 |
| `api` | Django (migrations, then the dev server with auto-reload) | 8000 |
| `worker` | Celery worker | — |

If a port is already used on your machine (for example a local Redis on 6379), set `POSTGRES_HOST_PORT`,
`REDIS_HOST_PORT` or `API_HOST_PORT` in `.env`. Also update `POSTGRES_PORT` / `REDIS_URL` there for tools run on
the host.

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
pnpm dev                          # admin on :3000, portal on :3001
```

Both apps proxy `/api/*` to `API_INTERNAL_URL` or `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).

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

## Settings

`backend/config/settings/` holds `base`, `local`, `staging`, `production` and `test`. Pick one with
`DJANGO_SETTINGS_MODULE`. Every secret comes from environment variables. See `.env.example` for the full list.
