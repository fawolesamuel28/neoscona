#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

read_env() {
  grep -m1 "^$1=" .env 2>/dev/null | cut -d= -f2- | tr -d '\r' | sed 's/^"//;s/"$//'
}

DATABASE_URL="$(read_env DATABASE_URL)"
SUPABASE_URL="$(read_env SUPABASE_URL)"
SUPABASE_DB_PASSWORD="$(read_env SUPABASE_DB_PASSWORD)"

if [[ -z "$DATABASE_URL" ]]; then
  if [[ -n "$SUPABASE_DB_PASSWORD" && "$SUPABASE_URL" == *supabase.co* ]]; then
    # Construct a DATABASE_URL for hosted Supabase projects
    PROJECT_HOST=$(echo "$SUPABASE_URL" | sed -E 's#https?://##')
    DATABASE_URL="postgresql://postgres:${SUPABASE_DB_PASSWORD}@db.${PROJECT_HOST.split('.')[0]}.supabase.co:5432/postgres" || true
  fi
fi

if [[ -z "$DATABASE_URL" ]]; then
  echo "DATABASE_URL is not set in .env. Please set it or SUPABASE_DB_PASSWORD + SUPABASE_URL." >&2
  exit 1
fi

echo "Applying migration: migrations/005_flutterwave.sql"
psql "$DATABASE_URL" -f migrations/005_flutterwave.sql
echo "Migration applied."
