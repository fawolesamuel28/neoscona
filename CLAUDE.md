# Neoscona Unified Platform — Engineering Notes

The "Twilio of AI automation for Africa." A multi-product SaaS; **Reva** (real-estate
lead qualification & nurture, powered by the "Amara AI" agent) is the live product.

## Entry points

- **Deployed app: `server.py` → `server:app`** (Procfile `web` → `scripts/start.sh` →
  `gunicorn server:app -k uvicorn.workers.UvicornWorker`). This is the unified
  marketing + platform + Reva console host. **All routing/middleware changes that
  ship must go here.**
- `app/main.py` is a separate/standalone Reva FastAPI app. It is **not** the deployed
  web entry point. Don't assume a change in one is reflected in the other.
- `worker`: `celery -A app.workers.celery_app worker` (background jobs in `app/jobs/`).

## Layout

- `app/core/` — auth, SSO, tenancy, middleware, dashboard websockets, state machine.
- `app/routers/` — `/api/*` REST routers. `app/services/` — business logic.
- `app/webhooks/` — inbound provider webhooks (whatsapp, instagram, vapi, voice/elevenlabs,
  calendly, paystack). `app/db/supabase.py` — the single Supabase client.
- `templates/` — Jinja pages served by `server.py` (dashboard, reva_*, marketing).
- `ai-leads-dashboard.html` (repo root) — the static **Reva Console** SPA, served at
  `/products/reva/console`.

## Reva Console routes (`server.py`)

`/products/reva/console` · `/products/reva/hot-leads` · `/products/reva/settings`.
All three share a **localhost dev bypass**: requests from `127.0.0.1`/`::1` skip
`page_session_ok()` so the sidebar is navigable locally without a session. Remote
requests require a valid SSO cookie (or `AUTH_DISABLED=1`, ignored in production).
Keep these three routes' guards consistent — the sidebar links cross-reference them.

## ⚠️ Multi-tenancy — the critical invariant

Reva is designed as a **single shared multi-tenant service** with logical isolation
(per-row `tenant_id` + Postgres RLS). This is the correct B2B SaaS model — do **not**
deploy a separate service per client.

**BUT isolation is currently DORMANT. Treat this as the top correctness risk.**
As of this writing:

- `app/core/tenant_middleware.py` (`TenantMiddleware`) is **not wired** into
  `server.py` or `app/main.py` (both only add CORS). The `tenant_token_var`
  contextvar it sets is never consumed.
- `app/db/supabase.py` uses a **service-role key**, which **bypasses RLS entirely**.
- `app/core/tenant.py` `apply_tenant_defaults()` / `get_default_tenant_id()` stamp a
  **single hardcoded `DEFAULT_TENANT_UUID`** on writes when no `tenant_id` is given.
- Onboarding (`app/services/onboarding.py`) **does** provision a distinct tenant per
  customer, but operational writes ignore it and fall back to the default tenant.
- RLS policies exist (see `schema.sql` / `migrations/008_default_tenant_rls.sql`), but
  some (`leads`, `conversation_logs`) **hardcode the default tenant UUID** rather than
  reading `current_setting('app.current_tenant_id')` like the other tables.

**Net effect: two different businesses' data can land in the same tenant. Do not
onboard a second paying client until isolation is activated.**

### Rules when touching tenant-scoped data (leads, conversations, usage, configs)

1. Resolve `tenant_id` from the **authenticated principal's membership**, never from
   the hardcoded default, on any user-facing read or write.
2. Every query against a tenant-scoped table MUST filter by `tenant_id` (or run under
   a client whose JWT/`current_setting` carries it so RLS applies).
3. New RLS policies use `current_setting('app.current_tenant_id', true)::uuid`, never a
   literal UUID.
4. Gate any tenancy change behind `/security-review` **and** `/verify` (sign in as two
   tenants, prove data does not cross). See `.claude/skills/tenant-isolation/`.

## Conventions

- **Line endings: LF.** Enforced by `.gitattributes` (`* text=auto eol=lf`). If you see
  a diff rewriting an entire file, it's a CRLF flip — run `git add --renormalize <file>`
  and commit only the real change. Do not hand-edit endings.
- Auth: `_auth_disabled()` honors `AUTH_DISABLED` only outside production.
  `SUPABASE_JWT_SECRET` must be ≥32 chars (the app refuses to start otherwise).
- Commit messages: conventional commits (`feat(reva):`, `style:`, `fix:`).
