"""alpha_hardware_kill_switch — fail-closed hardware kill switch.

Distinct from the operator-facing ``kill_switch_profile_runtime``
which is a soft discipline gate (daily P&L, consecutive losses).
This is a hardware-level surface:

* State lives in the SQLite hot store as a single row.
* Schema is validated on every read; ANY malformed row returns
  ``(tripped=True, reason="FAIL_CLOSED:...")`` — a corrupt kill
  switch never silently permits trading.
* Writes are transactional (SQLite provides atomicity by default),
  eliminating the "half-written state file" class of bug.
* Trip conditions: N consecutive errors, drawdown breach, operator
  explicit trip. Reset requires an operator identifier.

Placement in the pipeline
-------------------------
``check()`` is called once per broker submit attempt in
``public_equity_live_executor``. If tripped, the executor records
``executor_rejected`` with the fail-closed reason and skips the
submit — the compact authority receipt will surface it as
``failure_stage=roadguard``.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


_REQUIRED_KEYS: frozenset[str] = frozenset({"tripped", "errors", "reason"})
_DEFAULT: dict[str, Any] = {
    "tripped": False,
    "reason": None,
    "errors": 0,
    "peak_equity": None,
    "updated_at_ns": None,
    "updated_by": None,
}


def _now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)


def _ensure_table() -> None:
    from services import alpha_hot_store
    alpha_hot_store.init()
    with alpha_hot_store._connect() as con:  # noqa: SLF001
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS alpha_hw_kill_switch (
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                state_json    TEXT NOT NULL,
                updated_at_ns INTEGER NOT NULL
            )
            """
        )


def _max_errors() -> int:
    try:
        return int(os.environ.get("ALPHA_HW_KILL_MAX_ERRORS", "10"))
    except ValueError:
        return 10


def _max_drawdown_pct() -> float:
    try:
        return float(os.environ.get("ALPHA_HW_KILL_MAX_DRAWDOWN_PCT", "20"))
    except ValueError:
        return 20.0


def _read_raw() -> dict[str, Any]:
    """Load the current state row. On ANY schema violation return a
    fail-closed sentinel — never fabricate a healthy state."""
    _ensure_table()
    from services import alpha_hot_store
    try:
        with alpha_hot_store._connect() as con:  # noqa: SLF001
            row = con.execute(
                "SELECT state_json FROM alpha_hw_kill_switch WHERE id=1"
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[hw_kill] sqlite read failed, failing closed: %s", exc)
        return {
            "tripped": True, "errors": _max_errors(),
            "reason": f"FAIL_CLOSED:sqlite_read_failed:{type(exc).__name__}",
        }
    if row is None:
        return dict(_DEFAULT)
    raw = row["state_json"] or ""
    try:
        d = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return {
            "tripped": True, "errors": _max_errors(),
            "reason": f"FAIL_CLOSED:state_not_json:{type(exc).__name__}",
        }
    if not isinstance(d, dict) or not _REQUIRED_KEYS.issubset(d):
        return {
            "tripped": True, "errors": _max_errors(),
            "reason": "FAIL_CLOSED:state_missing_required_keys",
        }
    if not isinstance(d.get("tripped"), bool) or not isinstance(d.get("errors"), int):
        return {
            "tripped": True, "errors": _max_errors(),
            "reason": "FAIL_CLOSED:state_bad_types",
        }
    return d


def _write(state: dict[str, Any], *, updated_by: str = "system") -> None:
    """Overwrite the state row. Atomic by SQLite transaction."""
    _ensure_table()
    state = {**_DEFAULT, **state}
    state["updated_at_ns"] = _now_ns()
    state["updated_by"] = updated_by
    payload = json.dumps(state, sort_keys=True, default=str)
    from services import alpha_hot_store
    with alpha_hot_store._connect() as con:  # noqa: SLF001
        con.execute(
            "INSERT INTO alpha_hw_kill_switch(id, state_json, updated_at_ns) "
            "VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json, "
            "updated_at_ns=excluded.updated_at_ns",
            (payload, state["updated_at_ns"]),
        )


# ── Public API ───────────────────────────────────────────────────

def check() -> tuple[bool, Optional[str]]:
    """Return ``(tripped, reason)``. Cheap — one SQLite read."""
    d = _read_raw()
    return bool(d.get("tripped")), d.get("reason")


def trip(reason: str, *, by: str = "system") -> None:
    """Force the switch tripped. Idempotent."""
    d = _read_raw()
    d.update(tripped=True, reason=reason)
    _write(d, updated_by=by)
    logger.warning("[hw_kill] TRIPPED by=%s reason=%s", by, reason)


def reset(*, confirmed_by: str) -> None:
    """Clear the switch. Requires an operator identifier — enforced
    at the API layer, not here."""
    if not confirmed_by:
        raise ValueError("confirmed_by required")
    _write(dict(_DEFAULT), updated_by=confirmed_by)
    logger.warning("[hw_kill] RESET by=%s", confirmed_by)


def record_error(detail: str) -> None:
    """Increment consecutive-error counter; trip when threshold hit."""
    d = _read_raw()
    if d.get("tripped") and str(d.get("reason", "")).startswith("FAIL_CLOSED:"):
        # Already fail-closed — do not overwrite the diagnosis.
        return
    d["errors"] = int(d.get("errors", 0)) + 1
    threshold = _max_errors()
    if d["errors"] >= threshold:
        d.update(
            tripped=True,
            reason=f"{d['errors']} consecutive errors; last={detail!s:.100}",
        )
    _write(d)


def record_success() -> None:
    """Reset the consecutive-error counter (only when not tripped)."""
    d = _read_raw()
    if d.get("tripped"):
        return
    if d.get("errors"):
        d["errors"] = 0
        _write(d)


def record_equity(current_equity: float) -> None:
    """Update peak equity; trip when drawdown breaches the cap."""
    d = _read_raw()
    if d.get("tripped"):
        return
    peak = d.get("peak_equity")
    if peak is None or current_equity > peak:
        d["peak_equity"] = float(current_equity)
        _write(d)
        return
    if peak > 0:
        dd_pct = (peak - current_equity) / peak * 100.0
        if dd_pct >= _max_drawdown_pct():
            d.update(
                tripped=True,
                reason=f"drawdown_limit_exceeded:{dd_pct:.2f}pct_peak={peak:.2f}",
            )
            _write(d)


def get_state() -> dict[str, Any]:
    """Full state for the diagnostic panel."""
    return _read_raw()


__all__ = [
    "check", "trip", "reset", "record_error", "record_success",
    "record_equity", "get_state",
]
