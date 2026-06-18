#!/usr/bin/env bash
set -euo pipefail
# Railway injects PORT; fall back when running locally.
port="${PORT:-8080}"
if [[ "$port" == '$PORT' ]] || [[ ! "$port" =~ ^[0-9]+$ ]]; then
  port=8080
fi
exec gunicorn server:app \
  -k uvicorn.workers.UvicornWorker \
  --bind "0.0.0.0:${port}" \
  --workers 2 \
  --timeout 60
