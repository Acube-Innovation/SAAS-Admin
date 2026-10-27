#!/usr/bin/env bash
# Creates the application database roles. Idempotent.
#
# - app_owner: owns the schema and tables, runs migrations. Not a superuser and NOBYPASSRLS,
#              so FORCE ROW LEVEL SECURITY applies to it as well.
# - app_user:  the role the API and workers connect as. DML only, NOBYPASSRLS, never a table owner.
#
# Runs automatically from /docker-entrypoint-initdb.d on a fresh Docker volume; CI runs it with psql.
# Connection comes from the standard libpq env vars (PGHOST, PGPASSWORD, ...) plus POSTGRES_USER/POSTGRES_DB.
set -euo pipefail

: "${POSTGRES_OWNER_USER:?}" "${POSTGRES_OWNER_PASSWORD:?}" "${POSTGRES_APP_USER:?}" "${POSTGRES_APP_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 \
  --username "${POSTGRES_USER:-postgres}" \
  --dbname "${POSTGRES_DB:-postgres}" \
  -v owner_user="$POSTGRES_OWNER_USER" -v owner_password="$POSTGRES_OWNER_PASSWORD" \
  -v app_user="$POSTGRES_APP_USER" -v app_password="$POSTGRES_APP_PASSWORD" \
  -v db_name="${POSTGRES_DB:-postgres}" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
              :'owner_user', :'owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'owner_user') \gexec

SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
              :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec

-- The database owner owns the public schema (pg_database_owner), so migrations can create tables.
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db_name', :'owner_user') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'db_name', :'app_user') \gexec
SQL

echo "init-roles: roles $POSTGRES_OWNER_USER and $POSTGRES_APP_USER are ready"
