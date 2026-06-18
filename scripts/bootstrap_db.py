#!/usr/bin/env python3
"""Try Supabase Postgres pooler/direct URLs and apply platform migration."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

passwords = []
for key in ("SUPABASE_DB_PASSWORD",):
    v = os.getenv(key)
    if v:
        passwords.append(v.strip('"'))
passwords.append("neoscona2026")

hosts = [
    "db.oruazksvjbmfkuwriocb.supabase.co:5432",
    "aws-0-us-west-1.pooler.supabase.com:6543",
    "aws-1-us-west-1.pooler.supabase.com:6543",
    "aws-0-eu-central-1.pooler.supabase.com:6543",
    "aws-1-eu-central-1.pooler.supabase.com:6543",
]

import psycopg2

sql = (ROOT / "migrations" / "001_neoscona_platform.sql").read_text(encoding="utf-8")

for host in hosts:
    user = "postgres.oruazksvjbmfkuwriocb" if "pooler" in host else "postgres"
    for pw in passwords:
        url = f"postgresql://{user}:{pw}@{host}/postgres"
        try:
            conn = psycopg2.connect(url, connect_timeout=8)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.close()
            print(f"OK: applied migration via {host} (user={user})")
            sys.exit(0)
        except Exception as e:
            print(f"FAIL {host} user={user}: {e}")

print("Could not connect — run migrations/001_neoscona_platform.sql in Supabase SQL Editor.")
sys.exit(1)
