#!/usr/bin/env python3
import os
import psycopg2

url = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:neoscona2026@db.oruazksvjbmfkuwriocb.supabase.co:5432/postgres",
)
conn = psycopg2.connect(url)
cur = conn.cursor()
cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
print("public:", [r[0] for r in cur.fetchall()])
cur.execute("SELECT count(*) FROM auth.users")
print("auth.users:", cur.fetchone()[0])
conn.close()
