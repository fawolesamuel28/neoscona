#!/usr/bin/env python3
"""Apply a SQL file in migrations/ to the configured Supabase Postgres database."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

MIGRATIONS_DIR = ROOT / "migrations"
DEFAULT_MIGRATION = "001_neoscona_platform.sql"


def _resolve_migration(arg: str | None) -> Path:
    name = arg or DEFAULT_MIGRATION
    path = MIGRATIONS_DIR / Path(name).name
    if not path.exists():
        print(f"Migration not found: {path}")
        raise SystemExit(1)
    return path


def _build_url_from_password() -> str | None:
    password = os.getenv("SUPABASE_DB_PASSWORD")
    base = os.getenv("SUPABASE_URL", "")
    if not password or not base:
        return None
    ref = urlparse(base).netloc.split(".")[0]
    return f"postgresql://postgres:{password}@db.{ref}.supabase.co:5432/postgres"


def main() -> None:
    migration = _resolve_migration(sys.argv[1] if len(sys.argv) > 1 else None)
    db_url = (
        os.getenv("SUPABASE_DB_URL")
        or os.getenv("DATABASE_URL")
        or _build_url_from_password()
    )
    if not db_url:
        print("Set DATABASE_URL or SUPABASE_DB_PASSWORD + SUPABASE_URL in .env")
        raise SystemExit(1)

    try:
        import psycopg2
    except ImportError:
        os.system(f"{sys.executable} -m pip install psycopg2-binary -q")
        import psycopg2

    sql = migration.read_text(encoding="utf-8")
    print(f"Applying {migration.name} to {urlparse(db_url).hostname} ...")
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        print("Migration applied successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
