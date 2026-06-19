---
name: tenant-isolation
description: Audit Reva code for multi-tenant data-isolation correctness. Use BEFORE merging or deploying any change that reads or writes tenant-scoped data (leads, conversations, usage, agent configs, memberships), when adding an API route or webhook, when changing app/db/supabase.py, app/core/tenant*.py, or RLS/schema, or whenever the user asks to "check tenant isolation", "make sure clients can't see each other's data", or activate multi-tenancy.
---

# Tenant Isolation Audit

Reva is a shared multi-tenant SaaS. The cardinal sin is **cross-tenant leakage** —
Client A seeing Client B's leads/conversations. This skill audits a change (or the
whole codebase) for that risk and tells you exactly what to fix.

Read `CLAUDE.md` "Multi-tenancy" section first — isolation may still be **dormant**
(middleware unwired, service-role key bypassing RLS, writes defaulting to one tenant).

## When to run

- Before merging/deploying any change to tenant-scoped data paths.
- After adding a router (`app/routers/`), webhook (`app/webhooks/`), or service.
- When touching `app/db/supabase.py`, `app/core/tenant.py`, `app/core/tenant_middleware.py`,
  `schema.sql`, or any `migrations/*tenant*` / `*rls*` file.
- As a periodic full-codebase sweep.

## How to run the audit

Scope first: `git diff --name-only main...HEAD` for a change-scoped audit, or sweep
all of `app/routers app/services app/webhooks app/jobs app/db` for a full audit.

Work through every check below. For each finding report: **file:line · severity
(CRITICAL leak / HIGH / MEDIUM) · the problem · the concrete fix.** End with a verdict:
PASS (safe to ship) or BLOCK (leakage risk).

### 1. Writes carry the *authenticated* tenant — not the default
- Grep `apply_tenant_defaults`, `get_default_tenant_id`, `DEFAULT_TENANT_UUID`.
- CRITICAL if a **user-facing** write derives `tenant_id` from the hardcoded default
  instead of the request principal's membership. (Default is only acceptable for
  genuinely system-level/seed writes — justify each one.)

### 2. Reads filter by tenant
- For every Supabase query against a tenant-scoped table (`leads`, `lead_notes`,
  `conversation_logs`, `usage_counters`, `usage_events`, `agent_configs`, `memberships`),
  confirm a `.eq("tenant_id", ...)` filter **or** that it runs under a client whose JWT /
  `current_setting('app.current_tenant_id')` scopes it so RLS applies.
- CRITICAL: an unfiltered `select` on a tenant table run with the service-role key.

### 3. The client actually enforces RLS
- `app/db/supabase.py`: a static **service-role** client **bypasses RLS**. If isolation
  is meant to be active, reads/writes must go through a per-request client that carries
  the tenant JWT (see `tenant_token_var` in `app/core/tenant_middleware.py`), or every
  query must filter by `tenant_id` in app code.
- HIGH: service-role client used on a user-facing read path with no explicit tenant filter.

### 4. Tenant id comes from a trusted source
- Resolve tenant from the **verified** principal / membership, not from a raw
  client-supplied `x-tenant-id` header or query param unless cryptographically tied to
  the authenticated user. CRITICAL: trusting an unauthenticated `tenant_id` param for a read.
- Webhooks: confirm the inbound `tenant_id` (often a URL param) is validated against the
  provider/account, not blindly trusted.

### 5. RLS policies are parameterized, not hardcoded
- In `schema.sql` / migrations, tenant-scoped policies must use
  `current_setting('app.current_tenant_id', true)::uuid`, never a literal UUID.
- HIGH: a policy hardcoding `a0000000-...-0001` (known issue on `leads`,
  `conversation_logs`) — it pins the table to one tenant.

### 6. Background jobs & websockets are scoped
- `app/jobs/*` and `app/core/dashboard_ws.py`: confirm per-tenant work iterates tenants
  explicitly and websocket subscribers only receive their own tenant's events.
  CRITICAL: a dashboard event broadcast to all sockets regardless of tenant.

### 7. Cross-tenant object access (IDOR)
- Endpoints taking an id (`/leads/{id}`) must confirm the row belongs to the caller's
  tenant before returning/mutating it. CRITICAL: fetch-by-id with no tenant check.

## Verify behavior, don't just read code

After the static audit, prove it at runtime (pair with `/verify`): create two tenants,
write data as each, and confirm tenant A's session cannot read tenant B's leads,
conversations, or usage — via API and via the console. A green code review is not
isolation; a passing two-tenant cross-check is.

## Output template

```
TENANT ISOLATION AUDIT — <scope>
Verdict: PASS | BLOCK
Findings:
  [CRITICAL] app/routers/leads.py:88 — select on leads with no tenant filter under
             service-role key -> cross-tenant read. Fix: filter .eq("tenant_id", principal.tenant_id).
  [HIGH] schema.sql:1176 — leads RLS hardcodes default UUID. Fix: current_setting(...).
  ...
Runtime check: <done two-tenant cross-check? result>
```
