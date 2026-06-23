#!/usr/bin/env python3
"""Wipe tenant data and auth users, then create one new tenant + admin user.

USAGE (dry-run):
  python scripts/wipe_and_create.py --dry-run

To actually perform destructive actions pass --yes and optionally provide
new user/tenant details.

WARNING: This is destructive. Do not run unless you understand the impact.
"""
from __future__ import annotations

import os
import sys
import uuid
import json
import argparse
from dotenv import load_dotenv
import psycopg2
import httpx


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def run_sql(conn, sql: str):
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--yes", action="store_true", help="Perform destructive actions")
    p.add_argument("--dry-run", action="store_true", help="Show actions but don't execute")
    p.add_argument("--email", help="Email for the new admin user", default="admin+test@neoscona.local")
    p.add_argument("--password", help="Password for the new user", default=None)
    p.add_argument("--tenant-name", help="Tenant name", default="Test Tenant")
    p.add_argument("--company-name", help="Company name", default="Test Company")
    p.add_argument("--billing-email", help="Billing email", default="billing@neoscona.local")
    args = p.parse_args()

    load_dotenv()
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    database_url = os.getenv("DATABASE_URL")

    if not supabase_url or not service_key or not database_url:
        die("SUPABASE_URL, SUPABASE_SERVICE_KEY (or SUPABASE_KEY) and DATABASE_URL must be set in environment (.env)")

    if args.dry_run:
        print("DRY RUN — no changes will be made")

    if not args.yes and not args.dry_run:
        die("Pass --yes to perform destructive actions, or --dry-run to preview.")

    # Connect to Postgres
    print("Connecting to database...")
    conn = psycopg2.connect(database_url)

    # Find tenant-scoped tables
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.columns WHERE column_name='tenant_id' AND table_schema='public'")
        rows = cur.fetchall()
    tenant_tables = sorted({r[0] for r in rows})

    print("Tenant-scoped tables:", tenant_tables)

    # We will truncate memberships first, then other tenant tables, then tenants
    truncate_order = []
    if 'memberships' in tenant_tables:
        truncate_order.append('memberships')
    others = [t for t in tenant_tables if t not in ('memberships', 'tenants')]
    truncate_order.extend(others)
    if 'tenants' in tenant_tables:
        truncate_order.append('tenants')

    print("Truncate order:", truncate_order)

    if args.dry_run:
        print("Would run TRUNCATE on:", truncate_order)
    else:
        if truncate_order:
            sql = f"TRUNCATE TABLE {', '.join(['public.'+t for t in truncate_order])} RESTART IDENTITY CASCADE;"
            print("Executing:", sql)
            run_sql(conn, sql)
        else:
            print("No tenant-scoped tables found to truncate.")

    # Delete all auth users via Supabase Admin API
    headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
    users_endpoint = supabase_url.rstrip('/') + '/auth/v1/users'

    if args.dry_run:
        print(f"Would fetch users from {users_endpoint}")
    else:
        print("Fetching auth users...")
        with httpx.Client(timeout=30) as client:
            resp = client.get(users_endpoint, headers=headers)
            resp.raise_for_status()
            users = resp.json()
        print(f"Found {len(users)} users; deleting...")
        for u in users:
            uid = u.get('id') or u.get('user_id') or u.get('uid')
            if not uid:
                continue
            del_url = supabase_url.rstrip('/') + f'/auth/v1/admin/users/{uid}'
            if args.dry_run:
                print("Would DELETE", del_url)
            else:
                with httpx.Client(timeout=30) as client:
                    r = client.delete(del_url, headers=headers)
                    if r.status_code not in (200, 204):
                        print(f"Failed to delete user {uid}: {r.status_code} {r.text}")

    # Create new admin user via admin API
    new_email = args.email
    new_password = args.password or uuid.uuid4().hex[0:12]

    if args.dry_run:
        print(f"Would create new user {new_email} with password {new_password}")
        print("Would insert tenant and membership rows")
        return

    print("Creating new admin user via Supabase admin API...")
    payload = {"email": new_email, "password": new_password, "email_confirm": True}
    with httpx.Client(timeout=30) as client:
        r = client.post(supabase_url.rstrip('/') + '/auth/v1/admin/users', json=payload, headers=headers)
        r.raise_for_status()
        new_user = r.json()
    user_id = new_user.get('id') or new_user.get('user', {}).get('id') or new_user.get('user_id')
    print("Created user id:", user_id)

    # Insert tenant row
    tenant_name = args.tenant_name
    company_name = args.company_name
    billing_email = args.billing_email
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.tenants (name, company_name, billing_email) VALUES (%s, %s, %s) RETURNING id",
            (tenant_name, company_name, billing_email),
        )
        tenant_id = cur.fetchone()[0]
    conn.commit()
    print("Created tenant:", tenant_id)

    # Insert membership
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.memberships (user_id, tenant_id, role) VALUES (%s, %s, %s) RETURNING id",
            (user_id, tenant_id, 'admin'),
        )
        membership_id = cur.fetchone()[0]
    conn.commit()
    print("Created membership:", membership_id)

    print("Done. New admin credentials:")
    print(json.dumps({"email": new_email, "password": new_password, "user_id": user_id, "tenant_id": tenant_id}, indent=2))


if __name__ == '__main__':
    main()
