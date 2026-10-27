#!/usr/bin/env bash
# Regenerate the typed API client from the backend's OpenAPI schema.
# Run after changing any API endpoint or serializer; commit both generated files.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/frontend/packages/api-client"

cd "$ROOT/backend"
PYTHON="${PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python )}"
DJANGO_SETTINGS_MODULE=config.settings.test DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-schema-only}" \
  "$PYTHON" manage.py spectacular --validate --fail-on-warn --file "$OUT/openapi.yaml"

cd "$ROOT/frontend"
pnpm --filter @saas/api-client generate
echo "Updated $OUT/openapi.yaml and src/schema.d.ts"
