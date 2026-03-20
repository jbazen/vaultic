#!/bin/bash
# Disaster recovery: restore vaultic.db from Cloudflare R2.
#
# Run this on a fresh server BEFORE starting vaultic-api.service:
#
#   bash /home/ubuntu/vaultic/deploy/restore.sh
#   sudo systemctl start vaultic-api
#
# The script loads Litestream credentials from .env, then restores the
# latest snapshot + WAL segments from R2.

set -e

VAULTIC_DIR="/home/ubuntu/vaultic"
DB_PATH="$VAULTIC_DIR/data/vaultic.db"
CONFIG="$VAULTIC_DIR/deploy/litestream.yml"

# Load only Litestream env vars (avoids multi-line PEM issues)
export $(grep -E "^LITESTREAM_" "$VAULTIC_DIR/.env" | xargs)

if [ -z "$LITESTREAM_ACCESS_KEY_ID" ] || [ -z "$LITESTREAM_SECRET_ACCESS_KEY" ]; then
  echo "ERROR: LITESTREAM_ACCESS_KEY_ID or LITESTREAM_SECRET_ACCESS_KEY not found in .env"
  exit 1
fi

if [ -f "$DB_PATH" ]; then
  echo "WARNING: $DB_PATH already exists."
  read -p "Overwrite with R2 backup? (y/N): " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
  mv "$DB_PATH" "${DB_PATH}.pre-restore.$(date +%Y%m%d-%H%M%S)"
fi

mkdir -p "$(dirname "$DB_PATH")"

echo "Restoring from R2..."
/usr/local/bin/litestream restore -config "$CONFIG" "$DB_PATH"
echo "Restore complete: $DB_PATH"
echo "You can now start the service: sudo systemctl start vaultic-api"
