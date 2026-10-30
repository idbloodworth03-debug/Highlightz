#!/bin/bash
# Restart Highlightz when it stops answering, not only when it dies.
#
# WHY. On 2026-10-29 the server froze without crashing: a burst of Kick chat
# webhooks used up its open-file limit, accept() failed for 15 minutes, and the
# site answered nobody until the box was rebooted by hand. systemd's
# Restart=always only acts on a process that exits, and this one never did.
#
# WHAT. Run every minute by cron (deploy/highlightz-health.cron). Asks /health
# on the app's own port, skipping nginx so only the app is judged. After
# HEALTH_FAILS misses in a row (3 by default, so about three minutes; a deploy's
# restart costs at most one) it writes a marker into clips/ with what it saw,
# then restarts the service. The next start reads the marker and logs
# site_restarted_unresponsive as an error, which the alert tap emails to the
# owner (src/main.py). A service that is stopped on purpose is left alone.
#
# Everything is overridable through the environment, which is how the tests
# drive it (tests/test_freeze_guards.py).
set -u

URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"
STATE="${HEALTH_STATE:-/run/highlightz-health.fails}"
MARKER="${HEALTH_MARKER:-/opt/highlightz/clips/health_restart.json}"
LIMIT="${HEALTH_FAILS:-3}"
SERVICE="${HEALTH_SERVICE:-highlightz}"
RESTART="${HEALTH_RESTART_CMD:-systemctl restart $SERVICE}"

note() {
    if command -v logger >/dev/null 2>&1; then logger -t highlightz-health "$1"; else echo "highlightz-health: $1"; fi
}

if [ -z "${HEALTH_SKIP_ACTIVE_CHECK:-}" ] && ! systemctl is-active --quiet "$SERVICE"; then
    echo 0 > "$STATE"
    exit 0
fi

if curl -fsS -m 10 -o /dev/null "$URL" 2>/dev/null; then
    echo 0 > "$STATE"
    exit 0
fi

fails=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$STATE"
note "no answer from $URL ($fails of $LIMIT)"
if [ "$fails" -lt "$LIMIT" ]; then
    exit 0
fi

pid=$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || echo 0)
open_files=$(ls "/proc/${pid:-0}/fd" 2>/dev/null | wc -l)
printf '{"at": %s, "fails": %s, "open_files": %s}\n' "$(date +%s)" "$fails" "$open_files" > "$MARKER" 2>/dev/null \
    && chmod 644 "$MARKER" 2>/dev/null
note "restarting $SERVICE after $fails missed checks (open files: $open_files)"
echo 0 > "$STATE"
$RESTART
