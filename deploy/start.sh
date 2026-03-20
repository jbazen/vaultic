#!/bin/bash
# Vaultic startup wrapper: loads Litestream credentials from .env, then
# starts Litestream which streams WAL changes to R2 and wraps uvicorn.
#
# Using a wrapper script sidesteps the systemd EnvironmentFile ordering
# issue (EnvironmentFile is evaluated before ExecStartPre runs, so a
# dynamically written env file is always missing on first load).
set -e

VAULTIC_DIR="/home/ubuntu/vaultic"

# Export only Litestream vars from .env — grep is safe here because these
# two keys are simple strings with no special characters.
export $(grep -E "^LITESTREAM_" "$VAULTIC_DIR/.env" | xargs)

exec /usr/local/bin/litestream replicate \
  -config "$VAULTIC_DIR/deploy/litestream.yml" \
  -exec "$VAULTIC_DIR/.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000"
