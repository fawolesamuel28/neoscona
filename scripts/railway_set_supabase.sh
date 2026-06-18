#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

read_env() {
  grep -m1 "^$1=" .env 2>/dev/null | cut -d= -f2- | tr -d '\r' | sed 's/^"//;s/"$//'
}

SUPABASE_URL="$(read_env SUPABASE_URL)"
SUPABASE_ANON_KEY="$(read_env SUPABASE_ANON_KEY)"
SUPABASE_SERVICE_KEY="$(read_env SUPABASE_SERVICE_KEY)"
SUPABASE_KEY="${SUPABASE_SERVICE_KEY:-$(read_env SUPABASE_KEY)}"
DATABASE_URL="$(read_env DATABASE_URL)"
SUPABASE_DB_PASSWORD="$(read_env SUPABASE_DB_PASSWORD)"

if [[ -z "$SUPABASE_URL" || -z "$SUPABASE_ANON_KEY" ]]; then
  echo "Missing SUPABASE_URL or SUPABASE_ANON_KEY in .env" >&2
  exit 1
fi
if [[ -z "$SUPABASE_SERVICE_KEY" ]]; then
  echo "WARNING: SUPABASE_SERVICE_KEY missing in .env — signup provisioning will fail." >&2
  echo "Copy the service_role key from Supabase → Settings → API for oruazksvjbmfkuwriocb" >&2
fi

/home/theprinter/.railway/bin/railway variables \
  --set "SUPABASE_URL=${SUPABASE_URL}" \
  --set "SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY}" \
  --set "SUPABASE_SERVICE_KEY=${SUPABASE_SERVICE_KEY}" \
  --set "SUPABASE_KEY=${SUPABASE_KEY}" \
  --set "COOKIE_DOMAIN=.neoscona.xyz" \
  --set "COOKIE_SECURE=true" \
  --set "ENVIRONMENT=production" \
  --set "ALLOWED_ORIGINS=https://neoscona.xyz,https://www.neoscona.xyz,https://app.neoscona.xyz"

if [[ -n "$DATABASE_URL" ]]; then
  /home/theprinter/.railway/bin/railway variables --set "DATABASE_URL=${DATABASE_URL}"
elif [[ -n "$SUPABASE_DB_PASSWORD" && "$SUPABASE_URL" == *oruazksvjbmfkuwriocb* ]]; then
  /home/theprinter/.railway/bin/railway variables \
    --set "DATABASE_URL=postgresql://postgres:${SUPABASE_DB_PASSWORD}@db.oruazksvjbmfkuwriocb.supabase.co:5432/postgres"
fi

echo "Railway Supabase variables updated for ${SUPABASE_URL}"
