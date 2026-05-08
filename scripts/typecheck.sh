#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
#  Pre-deploy type-check gate
# ──────────────────────────────────────────────────────────────────────────
#  Runs mypy on /app/backend/services/ and compares the error list against
#  scripts/typecheck_baseline.txt. Exits 0 if the current report is a
#  subset of the baseline, non-zero on any regression.
#
#  Usage:
#    ./scripts/typecheck.sh            → run gate (CI / pre-deploy hook)
#    ./scripts/typecheck.sh --update   → snapshot current errors as the
#                                         new baseline. Do this after
#                                         intentionally fixing errors so
#                                         they can't regress.
#    ./scripts/typecheck.sh --list     → print the current error list,
#                                         no comparison.
#
#  Exit codes:
#    0  no regressions (or --update / --list ran successfully)
#    1  new or different errors appeared (gate failed)
#    2  mypy itself failed to run
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$REPO_ROOT/backend"
BASELINE="$SCRIPT_DIR/typecheck_baseline.txt"

MODE="${1:-check}"

# Normalise mypy output so day-to-day changes (line numbers drifting by a
# few rows, absolute vs relative paths) don't flip the gate. We keep the
# file path, error code, and error message — that's what actually defines
# "is this the same bug as before".
run_mypy() {
    cd "$BACKEND_DIR"
    # shellcheck disable=SC2015
    mypy --config-file=mypy.ini services/ 2>&1 || true
}

normalize() {
    # Drop line numbers (column numbers too) + blank "Found N errors" summary.
    # Sort so order doesn't matter. `grep || true` so empty input
    # (zero errors) doesn't propagate exit 1 through pipefail.
    (grep -E "^services/.*\[[a-z-]+\]$" || true) \
        | sed -E 's/:[0-9]+(:[0-9]+)?: /: /' \
        | sort -u
}

case "$MODE" in
    --update)
        echo "[typecheck] snapshotting current error list as baseline..."
        run_mypy | normalize > "$BASELINE"
        count=$(wc -l < "$BASELINE")
        echo "[typecheck] baseline updated: $count errors recorded."
        exit 0
        ;;
    --list)
        run_mypy | normalize
        exit 0
        ;;
    check|"")
        ;;
    *)
        echo "usage: $0 [--update | --list]" >&2
        exit 2
        ;;
esac

if [[ ! -f "$BASELINE" ]]; then
    echo "[typecheck] no baseline yet — run '$0 --update' first." >&2
    exit 2
fi

CURRENT="$(mktemp)"
trap 'rm -f "$CURRENT"' EXIT
run_mypy | normalize > "$CURRENT"

# New errors = lines present in CURRENT but not in BASELINE.
NEW=$(comm -23 "$CURRENT" "$BASELINE" || true)
# Resolved errors = lines in BASELINE but not in CURRENT. We report these
# so the dev knows to run --update and lock in the win.
GONE=$(comm -13 "$CURRENT" "$BASELINE" || true)

baseline_count=$(wc -l < "$BASELINE")
current_count=$(wc -l < "$CURRENT")
new_count=$(echo -n "$NEW" | grep -c '^' || true)
gone_count=$(echo -n "$GONE" | grep -c '^' || true)

echo "[typecheck] baseline: $baseline_count errors"
echo "[typecheck] current:  $current_count errors  (+$new_count new, -$gone_count fixed)"

if [[ $new_count -gt 0 ]]; then
    echo
    echo "🚫 NEW type errors (blocking deploy):"
    echo "$NEW" | head -50
    if [[ $new_count -gt 50 ]]; then
        echo "... and $((new_count - 50)) more"
    fi
    exit 1
fi

if [[ $gone_count -gt 0 ]]; then
    echo
    echo "✅ Fixed errors (run '$0 --update' to lock in):"
    echo "$GONE" | head -20
    if [[ $gone_count -gt 20 ]]; then
        echo "... and $((gone_count - 20)) more"
    fi
fi

echo
echo "[typecheck] gate passed."
