#!/usr/bin/env python3
"""Add a membership row for a user (uses SUPABASE_SERVICE_KEY).

Usage:
  python scripts/add_membership.py --user <user_id> --tenant <tenant_id> [--role admin]

Be careful: this uses the service-role key and bypasses RLS. Intended for
one-off admin fixes (granting a user access to a tenant).
"""
from __future__ import annotations

import argparse
from dotenv import load_dotenv
from app.db.supabase import get_supabase


def main():
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--user", required=True, help="Supabase user_id (sub)")
    p.add_argument("--tenant", required=True, help="Tenant UUID to grant membership for")
    p.add_argument("--role", default="admin", choices=["viewer", "agent", "admin", "owner"], help="Role to grant")
    args = p.parse_args()

    db = get_supabase()

    # Check existing
    res = db.table("memberships").select("id,role").eq("user_id", args.user).eq("tenant_id", args.tenant).limit(1).execute()
    if res.data:
        print(f"Membership already exists: {res.data[0]}")
        return

    payload = {"user_id": args.user, "tenant_id": args.tenant, "role": args.role}
    res = db.table("memberships").insert(payload).execute()
    if res.error:
        print("Failed to create membership:", res.error)
    else:
        print("Membership created:", res.data)


if __name__ == "__main__":
    main()
