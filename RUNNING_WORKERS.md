# Running Celery workers and processing Knowledge Base uploads (local / staging)

This document shows how to run the Celery worker and manually process knowledge uploads during local development or in a staging environment.

Prerequisites
- PostgreSQL accessible and `DATABASE_URL` in repo `.env` (or set in environment).
- Redis (or other Celery broker) reachable and `REDIS_URL` set in `.env`.
- Install Python deps: `pip install -r requirements.txt` (or your venv setup).
- Set embedding provider keys: `GEMINI_API_KEY` (or alternative embedding provider envs used by the app).

Start services (dev)
1. Start Redis (example using Docker):

```bash
# from project root
docker run -d --name redis-local -p 6379:6379 redis:7-alpine
```

2. Start a Celery worker (from repo root):

```bash
# in PowerShell (Windows):
$env:PYTHONPATH='.'; celery -A app.workers.celery_app worker --loglevel=info

# or Bash:
PYTHONPATH=. celery -A app.workers.celery_app worker --loglevel=info
```

3. (Optional) Start Celery beat if scheduled jobs are needed:

```bash
PYTHONPATH=. celery -A app.workers.celery_app beat --loglevel=info
```

Upload + process (manual)
- Upload a document via the frontend or directly with curl (example):

```bash
curl -H "Authorization: Bearer <ACCESS_TOKEN>" -F "file=@/path/to/doc.pdf" https://<your-host>/api/knowledge/upload
```

- The API will enqueue `process_knowledge_document` to Celery. With the worker running, you should see logs for chunking and embedding.

Manual processing (no Celery)
- If Celery isn't available, you can process a document inline (dev mode) by invoking the service directly. Replace `<DOC_ID>` and `<TENANT_ID>` accordingly:

```bash
# PowerShell
$env:PYTHONPATH='.'; python - <<'PY'
import asyncio
from app.services.knowledge import process_document
asyncio.run(process_document('<DOC_ID>', '<TENANT_ID>'))
PY
```

Troubleshooting
- Embedding failures: verify `GEMINI_API_KEY` and `EMBED_MODEL` env vars. If using Gemini/Google GenAI, ensure network egress and billing are enabled.
- DB errors: check `DATABASE_URL` and that the `knowledge_documents` and `knowledge_chunks` tables exist (run migrations).
- Worker not processing: ensure `REDIS_URL` matches both app and Celery worker; check worker logs for import errors.

CI / Staging recommendation
- Add a protected `SUPABASE_DATABASE_URL` (or equivalent) secret and a GitHub Actions workflow that can safely apply migrations.
- Run a smoke job in staging that uploads a small test doc and validates `knowledge_documents` moves to `ready` state.
