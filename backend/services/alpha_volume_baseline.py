"""alpha_volume_baseline — time-of-day RVOL, honest during warm-up.

Same-session ratio (current volume vs today's daily average) is what
Alpha used before. It flooded the panel with false setups because a
mid-morning candle compared to today's own mostly-empty average
almost always looked "high volume." Foundation v2.1's insight: RVOL
must compare THIS BAR to the SAME 15-minute UTC slot on prior
dates, and it must return ``None`` until we have a real baseline.

Design
------
* SQLite-backed (same hot-store DB). One row per (symbol, date, slot).
* ``rvol(symbol, dt, current_volume)`` returns ``(rvol, samples_used)``.
* When fewer than ``min_samples`` (default 5) matching historical
  rows exist, RVOL is ``None`` — we do NOT fabricate 1.0. Callers
  must treat ``None`` as "unknown volume" and either skip the setup
  or downgrade it (see Foundation's discernment.py — WATCH only,
  never ACTIONABLE, when RVOL is missing).
* The scanner call-site records the current bar's volume for
  future baseline use via ``observe()`` on every snapshot.
* Pruning is opportunistic — a lookback of 30 trading days keeps
  the table small (one row × universe × 26 RTH slots × 30 days ≈
  a few tens of thousands of rows).

Env config
----------
* ``ALPHA_RVOL_MIN_SAMPLES`` (default 5)
* ``ALPHA_RVOL_LOOKBACK_DAYS`` (default 30)
* ``ALPHA_RVOL_SLOT_MINUTES`` (default 15) — matches the current
  ``_snapshot_symbol`` bar cadence.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _min_samples() -> int:
    try:
        return max(1, int(os.environ.get("ALPHA_RVOL_MIN_SAMPLES", "5")))
    except ValueError:
        return 5


def _lookback_days() -> int:
    try:
        return max(5, int(os.environ.get("ALPHA_RVOL_LOOKBACK_DAYS", "30")))
    except ValueError:
        return 30


def _slot_minutes() -> int:
    try:
        return max(1, int(os.environ.get("ALPHA_RVOL_SLOT_MINUTES", "15")))
    except ValueError:
        return 15


def _ensure_table() -> None:
    from services import alpha_hot_store
    alpha_hot_store.init()
    with alpha_hot_store._connect() as con:  # noqa: SLF001
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS alpha_volume_baseline (
                symbol      TEXT NOT NULL,
                trade_date  TEXT NOT NULL,
                slot        TEXT NOT NULL,
                volume      REAL NOT NULL,
                PRIMARY KEY(symbol, trade_date, slot)
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS ix_avb_symbol_slot_date "
            "ON alpha_volume_baseline(symbol, slot, trade_date)"
        )


def _slot(dt: datetime) -> str:
    """Bucket ``dt`` into a UTC HH:MM slot on a configurable minute
    grid. Slot=15 means "09:15", "09:30", "09:45"; slot=5 means
    "09:15", "09:20", ..."""
    dt = dt.astimezone(timezone.utc)
    minutes = _slot_minutes()
    bucket_minute = (dt.minute // minutes) * minutes
    return f"{dt.hour:02d}:{bucket_minute:02d}"


def observe(symbol: str, dt: datetime, volume: float) -> None:
    """Record the current bar's volume so future days can use it as
    baseline. Idempotent — INSERT OR REPLACE ensures the last
    write wins for a given (symbol, date, slot).

    Non-raising: baseline maintenance must never break the scanner.
    """
    if not symbol or volume is None:
        return
    try:
        vol = float(volume)
    except (TypeError, ValueError):
        return
    if vol <= 0:
        return
    _ensure_table()
    from services import alpha_hot_store
    try:
        with alpha_hot_store._connect() as con:  # noqa: SLF001
            con.execute(
                "INSERT OR REPLACE INTO alpha_volume_baseline "
                "(symbol, trade_date, slot, volume) VALUES (?, ?, ?, ?)",
                (
                    symbol.upper(),
                    dt.astimezone(timezone.utc).date().isoformat(),
                    _slot(dt),
                    vol,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[rvol] observe failed for %s: %s", symbol, exc)


def rvol(symbol: str, dt: datetime, current_volume: float) -> tuple[Optional[float], int]:
    """Return ``(rvol, samples_used)``.

    ``rvol`` is ``None`` when we don't have enough historical
    samples at this slot (``< min_samples``). Callers must handle
    the warm-up case — never fabricate 1.0.
    """
    if not symbol or current_volume is None:
        return None, 0
    try:
        cur = float(current_volume)
    except (TypeError, ValueError):
        return None, 0
    _ensure_table()
    from services import alpha_hot_store
    try:
        with alpha_hot_store._connect() as con:  # noqa: SLF001
            rows = con.execute(
                "SELECT volume FROM alpha_volume_baseline "
                "WHERE symbol=? AND slot=? AND trade_date<? "
                "ORDER BY trade_date DESC LIMIT ?",
                (
                    symbol.upper(),
                    _slot(dt),
                    dt.astimezone(timezone.utc).date().isoformat(),
                    _lookback_days(),
                ),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[rvol] read failed for %s: %s", symbol, exc)
        return None, 0
    vals = [float(r["volume"]) for r in rows if r["volume"] is not None and float(r["volume"]) > 0]
    if len(vals) < _min_samples():
        return None, len(vals)
    avg = sum(vals) / len(vals)
    if avg <= 0:
        return None, len(vals)
    return cur / avg, len(vals)


def prune(*, keep_days: Optional[int] = None) -> int:
    """Delete rows older than ``keep_days`` trading days. Returns
    the number of rows deleted. Called opportunistically by the
    scheduler; not on the hot path."""
    _ensure_table()
    days = keep_days or (_lookback_days() * 2)
    from services import alpha_hot_store
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    with alpha_hot_store._connect() as con:  # noqa: SLF001
        cur = con.execute(
            "DELETE FROM alpha_volume_baseline WHERE trade_date < ?",
            (cutoff,),
        )
        return int(cur.rowcount or 0)


__all__ = ["observe", "rvol", "prune"]
