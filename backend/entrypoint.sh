#!/bin/sh
set -eu

alembic upgrade head
python -m app.backfill_complaints
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
