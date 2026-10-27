# Schedule Confirmation SaaS

## This repository (SAAS-Admin)
- Backend (Django API serving both the admin console and the client portal), the super admin
  console (`frontend/apps/admin`), shared frontend packages, infrastructure and docs.
- The client portal frontend lives in SAAS-Client: https://github.com/Acube-Innovation/SAAS-Client
  It keeps its own copy of `packages/ui`, `packages/api-client` and `packages/config`; make
  changes to those in both repositories.

Multi-tenant SaaS that reminds our clients' customers (gas agencies first)
via WhatsApp and later AI voice, and records their answers.

## Stack (do not change without asking)
- Backend: Python 3.12+, Django LTS, Django REST Framework, drf-spectacular
- DB: PostgreSQL 16+ with Row-Level Security; UUID primary keys
- Jobs: Celery + Redis; files on S3; email via SES
- Frontend: Next.js App Router + TypeScript, Tailwind, React Hook Form + Zod,
  TanStack Query, Turborepo + pnpm
- Tests: pytest/pytest-django, Vitest, Playwright
- Infra: Docker Compose locally; Terraform on AWS ap-south-1

## Rules
- Field names, validation and workflows: follow docs/admin-console-spec.md
  Sections 2 and 3 exactly. Same snake_case names in DB and API.
- Every tenant table has tenant_id and an RLS policy; app DB role cannot
  bypass RLS. Never query across tenants in application code.
- Every write action creates an AuditLog entry (before/after JSON).
- Admin API under /api/admin/, requires platform staff + completed 2FA.
- All UI text goes through the translation layer (English first).
- Secrets only from environment variables. Never commit secrets.
- Write tests with every feature; all tests must pass before you finish.
- Small, focused changes. Ask before adding new dependencies.
- Client portal: follow docs/client-portal-spec.md. Message content (templates,
  voice scripts, question flows) is view-only for clients: no create/edit APIs.
