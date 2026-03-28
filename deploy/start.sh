#!/bin/bash
# Vaultic startup wrapper: loads Litestream credentials from .env, then
# starts Litestream which streams WAL changes to R2 and wraps uvicorn.
#
# Using a wrapper script sidesteps the systemd EnvironmentFile ordering
# issue (EnvironmentFile is evaluated before ExecStartPre runs, so a
# dynamically written env file is always missing on first load).
set -e

VAULTIC_DIR="/home/ubuntu/vaultic"

# Export only Litestream vars from .env — read line-by-line to safely
# handle values with spaces or special characters.
while IFS='=' read -r key value; do
  case "$key" in
    LITESTREAM_*) export "$key=$value" ;;
  esac
done < "$VAULTIC_DIR/.env"

exec /usr/local/bin/litestream replicate \
  -config "$VAULTIC_DIR/deploy/litestream.yml" \
  -exec "$VAULTIC_DIR/.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000"
