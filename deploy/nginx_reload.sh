#!/bin/bash
# Reload nginx and confirm the running master actually took the new config (#167).
#
# Usage: nginx_reload.sh <pid file> <error log> <reload command...>
# The deploy runs: sudo bash deploy/nginx_reload.sh /run/nginx.pid \
#                    /var/log/nginx/error.log systemctl reload nginx
#
# `systemctl reload nginx` only signals the master and returns 0 even when the
# master then rejects the config (for example a changed limit_req_zone key,
# which nginx -t accepts): it logs [emerg] and keeps serving the old config.
# nginx starts new worker processes only after it has accepted a config, so a
# worker PID that wasn't there before the reload means the reload applied.
# Exits 0 when it did, 1 otherwise, printing what the error log said.
set -u

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <pid file> <error log> <reload command...>" >&2
  exit 2
fi
PID_FILE=$1
ERROR_LOG=$2
shift 2

MASTER=$(cat "$PID_FILE" 2>/dev/null)
if [ -z "$MASTER" ]; then
  echo "nginx reload check: no master PID in $PID_FILE"
  exit 1
fi
BEFORE=$(pgrep -P "$MASTER" | sort)
LOG_LINES=$(wc -l < "$ERROR_LOG" 2>/dev/null || echo 0)

if ! "$@"; then
  echo "nginx reload check: '$*' failed"
  exit 1
fi

for _ in $(seq 1 20); do
  NEW=$(comm -13 <(echo "$BEFORE") <(pgrep -P "$MASTER" | sort))
  if [ -n "$NEW" ]; then
    echo "nginx reload applied (new workers: $(echo $NEW))"
    exit 0
  fi
  sleep 0.5
done

echo "nginx reload NOT applied: no new worker processes after 10s, nginx kept the old config"
if [ -r "$ERROR_LOG" ]; then
  # Lines written since the reload; the whole tail if the log was rotated meanwhile.
  NOW_LINES=$(wc -l < "$ERROR_LOG")
  if [ "$NOW_LINES" -ge "$LOG_LINES" ]; then
    tail -n +"$((LOG_LINES + 1))" "$ERROR_LOG" | tail -n 20
  else
    tail -n 20 "$ERROR_LOG"
  fi
fi
exit 1
