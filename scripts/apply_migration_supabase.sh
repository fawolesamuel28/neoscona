#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

MIGRATION="${1:-}"
if [[ -z "$MIGRATION" ]]; then
  echo "usage: $0 <path/to/migration.sql>" >&2
  exit 1
fi
if [[ ! -f "$MIGRATION" ]]; then
  echo "Migration file not found: $MIGRATION" >&2
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "psql not found. Install postgresql-client (e.g. 'sudo apt-get install -y postgresql-client')." >&2
  exit 1
fi

read_env() {
  # `|| true` so a missing key returns empty instead of tripping `set -e` via pipefail.
  { grep -m1 "^$1=" .env 2>/dev/null | cut -d= -f2- | tr -d '\r' | sed 's/^"//;s/"$//'; } || true
}

DATABASE_URL="$(read_env DATABASE_URL)"
SUPABASE_URL="$(read_env SUPABASE_URL)"
SUPABASE_DB_PASSWORD="$(read_env SUPABASE_DB_PASSWORD)"
SUPABASE_REGION="$(read_env SUPABASE_REGION)"

# Project ref lives in SUPABASE_URL (https://<ref>.supabase.co).
PROJECT_REF=""
[[ "$SUPABASE_URL" == *supabase.co* ]] && PROJECT_REF=$(echo "$SUPABASE_URL" | sed -E 's#https?://##; s#\..*##')

# Fall back to the password embedded in an existing DATABASE_URL.
if [[ -z "$SUPABASE_DB_PASSWORD" && -n "$DATABASE_URL" ]]; then
  SUPABASE_DB_PASSWORD=$(echo "$DATABASE_URL" | sed -E 's#^[^:]+://[^:]+:([^@]+)@.*#\1#')
fi

# Prefer the IPv4 connection pooler: the direct db.<ref>.supabase.co host is
# IPv6-only and unreachable from IPv4-only networks (CI, WSL2, etc.).
if [[ -n "$PROJECT_REF" && -n "$SUPABASE_DB_PASSWORD" && -n "$SUPABASE_REGION" ]]; then
  CONN="postgresql://postgres.${PROJECT_REF}:${SUPABASE_DB_PASSWORD}@aws-0-${SUPABASE_REGION}.pooler.supabase.com:5432/postgres"
elif [[ -n "$DATABASE_URL" ]]; then
  CONN="$DATABASE_URL"
elif [[ -n "$PROJECT_REF" && -n "$SUPABASE_DB_PASSWORD" ]]; then
  CONN="postgresql://postgres:${SUPABASE_DB_PASSWORD}@db.${PROJECT_REF}.supabase.co:5432/postgres"
else
  echo "No connection info. Set DATABASE_URL, or SUPABASE_URL + SUPABASE_DB_PASSWORD (plus SUPABASE_REGION for the IPv4 pooler)." >&2
  exit 1
fi

echo "Applying migration: $MIGRATION"
psql "$CONN" -f "$MIGRATION"
echo "Migration applied."
