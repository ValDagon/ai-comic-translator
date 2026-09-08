#!/bin/sh
# Cloud Run: one container — background worker + HTTP API (same disk/SQLite).
# Set as container command: /app/scripts/cloudrun-entrypoint.sh
# Needs min instances ≥ 1 for steady job processing; prefer VM + docker compose for prod.
#
# Cloud Run's "container command" overrides the image ENTRYPOINT, so this
# script must itself go through /docker-entrypoint.sh to get the same
# ownership self-heal + drop-to-appuser behavior as the compose/VM path.

set -e
cd /app
exec /docker-entrypoint.sh sh -c '
    python worker.py &
    exec uvicorn api:app --host 0.0.0.0 --port "${PORT:-8000}"
'
