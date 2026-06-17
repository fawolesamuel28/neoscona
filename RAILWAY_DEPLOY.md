# Deploy Neoscona (neoscona.xyz) on Railway

The marketing site is a small Flask app. The landing page (`/`) needs no database;
the blog (`/blog`) reads from Postgres and degrades to an empty list if the DB is
unavailable, so the site is always up.

## Architecture (free-tier friendly)

- **One** Railway service: the Flask web app (no Railway Postgres plugin).
- The blog DB lives on **Supabase** (the same provider Reva uses), reached via a
  standard Postgres connection string in `DATABASE_URL`. This keeps Railway to a
  single resource so it fits the free plan.

## Files that make it deploy

- `requirements.txt` — Flask, gunicorn, psycopg2
- `Procfile` / `railway.toml` — gunicorn start command + `/healthz` healthcheck
- `app.py` — reads `DATABASE_URL` from env; binds to `$PORT`; `/` needs no DB
- `supabase_blog.sql` — creates the `posts` table on Supabase (run once)

## 1. One-time setup

```bash
# from the neoscona/ directory
railway login
railway init            # create project "neoscona"  (or: railway link)
```

> If `railway init` detects the `psycopg2` dependency and offers to add Postgres,
> **decline** — we use Supabase, not a Railway database. Adding one would push the
> account over the free-plan resource limit.

## 2. Create the blog table on Supabase

In the Supabase dashboard → **SQL Editor**, run `supabase_blog.sql` (creates
`public.posts` and seeds one manifesto post). Then grab the connection string:
**Supabase → Project Settings → Database → Connection string → URI** (use the
pooled "Transaction"/port-6543 string for serverless-style web apps; the direct
5432 string also works). It looks like:

```
postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
```

## 3. Environment variables (Railway → service → Variables)

| Var | Needed | Notes |
|-----|--------|-------|
| `DATABASE_URL` | blog | The Supabase Postgres connection string from step 2 |
| `ADMIN_TOKEN`  | recommended | Gates `/admin?token=…`; without it `/admin` is open (dev only) |
| `PORT`         | auto | Set by Railway |

```bash
railway variables --set 'DATABASE_URL=postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres'
railway variables --set 'ADMIN_TOKEN=<a-long-random-string>'
```

## 4. Deploy

```bash
railway up --detach
```

Confirm the generated `*.up.railway.app` URL serves the new landing page, and
`/healthz` returns `{"status":"ok"}`.

## 5. Custom domain (Namecheap: www served + apex redirect)

Railway → service → **Settings → Networking → Custom Domain** → add
`www.neoscona.xyz`; Railway shows a CNAME target like `xxxx.up.railway.app`.

At **Namecheap → neoscona.xyz → Advanced DNS**:
- Remove the old GitHub Pages records (the four `A @ → 185.199.x.x` and `CNAME www → *.github.io`).
- Add `CNAME` · Host `www` · Value `<railway target>` · TTL Automatic.
- Add `URL Redirect` · Host `@` · Value `https://www.neoscona.xyz` · **Permanent (301)**.

The `CNAME` file in this repo is a GitHub Pages artifact and is harmless on Railway.

## Local run

```bash
pip install -r requirements.txt
export DATABASE_URL='postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres'  # optional; blog empty without it
python app.py            # http://localhost:5000
```
