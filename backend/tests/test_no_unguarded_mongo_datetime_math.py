"""CI Guard: prevent regressions of the Mongo tz-naive datetime bug.

Greps the backend source for ``datetime.now(timezone.utc) - <var>``
and ``datetime.now(timezone.utc) [<>=] <var>`` patterns. For each
hit, walks back a few lines to confirm that ``<var>`` is one of:

* a tz-aware constant (`datetime.now(timezone.utc)` itself, etc.)
* defined locally in this function (`<var> = datetime.now(...)`)
* normalized through ``ensure_utc(...)`` or ``.replace(tzinfo=...)``
* a known in-process state field (`self._tripped_at`, `_cached_at`,
  `started`, etc. — populated only from ``datetime.now(timezone.utc)``)

If the pattern exists but no normalization is found within the
preceding ~25 lines of the same function, the test fails with a
clear pointer to the offending file & line. New code that bypasses
``ensure_utc()`` will surface here before it ships.

This is intentionally a heuristic — false positives are added to
``ALLOWLIST`` rather than relaxing the matcher, so we keep the
strict default and only excuse known-safe patterns.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent  # /app/backend

SCAN_DIRS = ["services", "routes", "ai_core"]
SKIP_FILES = {"datetime_utils.py"}  # the helper itself is the source

# (var pattern) — these are the local/in-process names we trust.
# Anything that isn't one of these AND isn't normalized must use
# ensure_utc().
SAFE_LOCAL_NAMES = {
    "now", "now_utc", "started", "start_time", "_cached_at", "cached_at",
    "_tripped_at", "tripped_at", "since", "cutoff", "cutoff_old", "cutoff_new",
    "cutoff_recent", "cutoff_24h", "cutoff_1w", "cutoff_date", "rec_since",
    "seven_days_ago", "twelve_months_ago", "week_ago", "today", "day", "to_date",
    "from_date", "stale_ts", "fallback", "current_expires", "past", "deadline",
    "ts", "_ts", "now_dt", "last",
}

# Match ``(datetime.now(timezone.utc) - VAR)`` or ``VAR <op> datetime.now(...)``
# Negative-lookahead on ``timedelta(...)`` since subtracting a timedelta
# is always safe (the result is the operator we WANT — a tz-aware shifted
# datetime). We're guarding against subtracting another *datetime* that
# might be tz-naive.
PATTERN_SUB = re.compile(
    r"datetime\.now\(timezone\.utc\)\s*-\s*(?!timedelta\b)([A-Za-z_][A-Za-z_0-9\.\[\]'\"]*)"
)
PATTERN_CMP_RIGHT = re.compile(
    r"([A-Za-z_][A-Za-z_0-9\.\[\]'\"]*)\s*[<>]=?\s*datetime\.now\(timezone\.utc\)"
)
PATTERN_CMP_LEFT = re.compile(
    r"datetime\.now\(timezone\.utc\)\s*[<>]=?\s*([A-Za-z_][A-Za-z_0-9\.\[\]'\"]*)"
)

# Files explicitly allow-listed because the variable is provably
# tz-aware via in-process construction. Keys are file paths relative
# to /app/backend; values are sets of line numbers.
ALLOWLIST: dict[str, set[int]] = {
    # All four ``datetime.now(timezone.utc) - started`` are inside
    # ``run`` / ``run_parallel`` where ``started`` is set on the
    # immediately-preceding line.
    "services/providerrouter.py": {175, 279, 326, 334, 338},
    # _tripped_at is in-memory state mutated only by tz-aware writes.
    "ai_core/kill_switch.py": {206, 236},
    # Already pre-guarded with ``.replace(tzinfo=timezone.utc)`` directly
    # above the math (not via ensure_utc but functionally equivalent).
    "services/ops_snapshot.py": {154},
    "services/watchlist_intelligence_service.py": {129},
    "routes/admin_guard_shadow.py": {300},
    "routes/auth.py": {444},
    # Internal _parse_iso normalizes tzinfo before this line.
    "services/firewall.py": {127},
    # Local cached_at populated from datetime.now(timezone.utc).
    "services/fred_service.py": {90, 237},
    "services/guard_policy_store.py": {226},
    # start_time is local to function body.
    "services/memory_training_service.py": {296},
    # _last_symbol_fire_at() returns a datetime that is normalized to
    # UTC via ``ts.replace(tzinfo=timezone.utc)`` before returning; the
    # matcher only inspects the immediate window, not the callee.
    "services/public_equity_live_executor.py": {563},
}


def _is_normalized(window: str) -> bool:
    """Return True if the preceding window contains an explicit
    tzinfo fix or an ensure_utc() call."""
    return (
        "ensure_utc(" in window
        or "replace(tzinfo=" in window
        or "tzinfo=timezone.utc" in window
        or "_parse_iso(" in window
    )


def _is_safe_var(name: str) -> bool:
    base = name.split(".")[0].split("[")[0]
    return base in SAFE_LOCAL_NAMES


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return list of (lineno, var, raw_line) hits that look unsafe."""
    text = path.read_text()
    lines = text.split("\n")
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines, start=1):
        for pattern in (PATTERN_SUB, PATTERN_CMP_RIGHT, PATTERN_CMP_LEFT):
            m = pattern.search(line)
            if not m:
                continue
            var = m.group(1)
            if "datetime.now" in var:
                continue  # comparing now() to itself
            if _is_safe_var(var):
                continue
            # Walk back up to 25 lines (function-scope heuristic) to
            # see if this var was assigned from a tz-aware constructor
            # or normalized via ensure_utc.
            window = "\n".join(lines[max(0, i - 25):i])
            if _is_normalized(window):
                continue
            # Also accept ``var = datetime.now(timezone.utc)`` or
            # ``+ timedelta(...)`` written shortly before.
            if re.search(
                rf"\b{re.escape(var.split('.')[0])}\s*=\s*datetime\.now\(timezone\.utc\)",
                window,
            ):
                continue
            hits.append((i, var, line.strip()))
    return hits


def test_no_unguarded_mongo_datetime_math():
    """Fail if any new code subtracts/compares a Mongo-derived
    datetime against ``datetime.now(timezone.utc)`` without first
    passing it through ``ensure_utc()``.

    The fix when this test fails:

        from services.datetime_utils import ensure_utc
        # ...
        var = ensure_utc(var)
        if var is not None:
            delta = (datetime.now(timezone.utc) - var).total_seconds()
    """
    failures: list[str] = []
    for sub in SCAN_DIRS:
        for py in (ROOT / sub).rglob("*.py"):
            if py.name in SKIP_FILES:
                continue
            rel = str(py.relative_to(ROOT))
            allowed = ALLOWLIST.get(rel, set())
            for lineno, var, raw in _scan_file(py):
                if lineno in allowed:
                    continue
                failures.append(f"{rel}:{lineno} — `{var}` not normalized | {raw}")

    if failures:
        msg = (
            "Unguarded Mongo datetime math detected. Pass each variable "
            "through `services.datetime_utils.ensure_utc()` before doing "
            "math against `datetime.now(timezone.utc)`:\n  - "
            + "\n  - ".join(failures)
        )
        pytest.fail(msg)
