#!/usr/bin/env bash
# RISEDUAL Sovereign Sidecar — external liveness watcher.
#
# Per MC's 2026-05-14 hardening note (Fix #3): even with an in-process
# watchdog thread that calls os._exit on stale ticks, certain freeze
# classes can wedge the entire interpreter (GIL deadlocks, fork inside
# a thread, native-extension SIGSTOP, etc.) such that no Python thread
# runs. This shell loop runs in a separate process — same supervisor,
# different process — and pkill -9's the sidecar when the liveness
# file goes stale past the threshold. supervisor's autorestart=true
# brings the sidecar back within startsecs.
#
# Args (env vars):
#   LIVENESS_FILE        path to the file touched by the sidecar each tick
#   STALE_THRESHOLD_SEC  age in seconds after which we kill (default 120)
#   POLL_INTERVAL_SEC    polling cadence (default 30)
#   KILL_PATTERN         pkill -f pattern that identifies the sidecar
set -euo pipefail

: "${LIVENESS_FILE:?LIVENESS_FILE env var required}"
: "${KILL_PATTERN:?KILL_PATTERN env var required (pkill -f target)}"
STALE_THRESHOLD_SEC="${STALE_THRESHOLD_SEC:-120}"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-30}"

echo "[liveness-watcher] starting: file=$LIVENESS_FILE threshold=${STALE_THRESHOLD_SEC}s poll=${POLL_INTERVAL_SEC}s pattern='$KILL_PATTERN'"

while true; do
    if [[ -f "$LIVENESS_FILE" ]]; then
        mtime="$(stat -c %Y "$LIVENESS_FILE" 2>/dev/null || echo 0)"
        now="$(date +%s)"
        age=$(( now - mtime ))
        if (( age > STALE_THRESHOLD_SEC )); then
            echo "[liveness-watcher] STALE: $LIVENESS_FILE age=${age}s > ${STALE_THRESHOLD_SEC}s — pkill -9 -f '$KILL_PATTERN'"
            pkill -9 -f "$KILL_PATTERN" || echo "[liveness-watcher] pkill returned non-zero (process may be gone already)"
            # Give supervisor a beat to respawn before we poll again.
            sleep "$POLL_INTERVAL_SEC"
        fi
    else
        echo "[liveness-watcher] liveness file not yet present: $LIVENESS_FILE (sidecar may be starting)"
    fi
    sleep "$POLL_INTERVAL_SEC"
done
