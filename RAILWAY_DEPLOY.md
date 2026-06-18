# Deploy Neoscona (neoscona.xyz) on Railway

The unified platform is a **FastAPI** app (`server.py`). It serves the marketing
site, console (`/dashboard`), SSO auth bridge, and Reva API routes. The landing
page (`/`) needs no database; `/blog` degrades to an empty list if the DB is
unavailable.

## Architecture (free-tier friendly)

- **One** Railway web service: `gunicorn server:app` with `uvicorn.workers.UvicornWorker`.
- Optional **worker** service: Celery (`Procfile` → `worker:` line) if background jobs are enabled.
- Postgres (blog, leads, etc.) lives on **Supabase**, reached via `DATABASE_URL` or
  `SUPABASE_URL` / service keys — not a Railway Postgres plugin.

## Files that make it deploy

| File | Role |
|------|------|
| `server.py` | FastAPI entrypoint (`server:app`) — marketing pages, SSO, Reva routers |
| `Procfile` | `web:` gunicorn + `worker:` celery |
| `railway.toml` | start command + `/healthz` healthcheck |
| `Dockerfile` | `scripts/start.sh` → same gunicorn command |
| `scripts/start.sh` | `gunicorn server:app -k uvicorn.workers.UvicornWorker …` |
| `requirements.txt` | FastAPI, gunicorn, uvicorn, supabase, etc. |
| `supabase_blog.sql` | Creates the `posts` table on Supabase (run once, optional) |

Start command (all deploy paths):

```bash
gunicorn server:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --workers 2 --timeout 60
```

Healthcheck: `GET /healthz` → `{"status":"ok"}` (deep check at `/api/health`).

## 1. One-time setup

```bash
# from the neoscona/ directory
railway login
railway init            # create project "neoscona"  (or: railway link)
```

> If Railway offers to add a Postgres plugin, **decline** unless you explicitly
> want a second database — Supabase is the primary store.

## 2. Create the blog table on Supabase (optional)

In the Supabase dashboard → **SQL Editor**, run `supabase_blog.sql` (creates
`public.posts` and seeds one manifesto post). Then grab the connection string:
**Supabase → Project Settings → Database → Connection string → URI**.

```
postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
```

## 3. Environment variables (Railway → service → Variables)

| Var | Needed | Notes |
|-----|--------|-------|
| `PORT` | auto | Set by Railway |
| `SUPABASE_URL` | **auth + data** | Auth/IdP project URL, e.g. `https://<ref>.supabase.co`. JWKS at `<url>/auth/v1/.well-known/jwks.json` |
| `SUPABASE_ANON_KEY` | **auth** | Anon public key — browser login/signup and GoTrue refresh |
| `SUPABASE_SERVICE_KEY` | recommended | Server-side writes (leads, billing, etc.) |
| `SUPABASE_JWT_SECRET` | fallback | Only for legacy HS256 tokens; ES256/RS256 verify via JWKS with no secret |
| `COOKIE_DOMAIN` | **auth (prod)** | `.neoscona.xyz` — shares `nsc_access` across subdomains. Empty for localhost |
| `COOKIE_SECURE` | **auth (prod)** | `true` in production (HTTPS). `false` only for local http dev |
| `ALLOWED_ORIGINS` | prod | Comma-separated credentialed CORS origins, e.g. `https://neoscona.xyz,https://app.neoscona.xyz` |
| `DATABASE_URL` | optional | Blog Postgres connection string (if wired) |
| `REDIS_URL` | optional | Dashboard WebSocket fan-out + Celery broker |
| `ADMIN_TOKEN` | optional | Gates `/admin?token=…` |

> **Single IdP:** `SUPABASE_URL` / `SUPABASE_ANON_KEY` must be the **same**
> Supabase auth project across the console and every product surface so one login
> works everywhere.

```bash
railway variables --set 'SUPABASE_URL=https://<auth-ref>.supabase.co'
railway variables --set 'SUPABASE_ANON_KEY=<anon-public-key>'
railway variables --set 'COOKIE_DOMAIN=.neoscona.xyz'
railway variables --set 'COOKIE_SECURE=true'
```

## 4. Deploy

```bash
railway up --detach
```

Confirm the generated `*.up.railway.app` URL serves the landing page and
`/healthz` returns `{"status":"ok"}`.

## 5. Custom domain (Namecheap: www served + apex redirect)

Railway → service → **Settings → Networking → Custom Domain** → add
`www.neoscona.xyz`; Railway shows a CNAME target like `xxxx.up.railway.app`.

At **Namecheap → neoscona.xyz → Advanced DNS**:
- Remove the old GitHub Pages records (the four `A @ → 185.199.x.x` and `CNAME www → *.github.io`).
- Add `CNAME` · Host `www` · Value `<railway target>` · TTL Automatic.
- Add `URL Redirect` · Host `@` · Value `https://www.neoscona.xyz` · **Permanent (301)**.

## Local run

```bash
pip install -r requirements.txt

export SUPABASE_URL='https://<auth-ref>.supabase.co'
export SUPABASE_ANON_KEY='<anon-public-key>'
export COOKIE_DOMAIN=''        # host-only cookie for localhost
export COOKIE_SECURE='false'   # allow cookies over http locally

# Dev server (uvicorn — gunicorn needs Linux):
uvicorn server:app --reload --host 0.0.0.0 --port 8000
# or:
python server.py                 # http://localhost:8000
```

Production-parity local boot (Linux/macOS only):

```bash
gunicorn server:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers 2 --timeout 60
```
