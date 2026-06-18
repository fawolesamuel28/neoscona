#!/usr/bin/env bash
set -euo pipefail
exec gunicorn server:app \
  -k uvicorn.workers.UvicornWorker \
  --bind "0.0.0.0:${PORT:-8080}" \
  --workers 2 \
  --timeout 60
