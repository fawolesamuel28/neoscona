web: gunicorn server:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --workers 2 --timeout 60
worker: celery -A app.workers.celery_app worker --loglevel=info
