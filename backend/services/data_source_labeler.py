"""Data-source labeler — derived `data_source` field for read APIs.

Why this exists
---------------
Public-launch storefront data starts on a specific cutover date.
Anything before that point is **backtest depth** — it's labeled
output from running our pipelines over historical price data, not
positions actually held in real time. Pre-cutover rows are
legitimately useful (they prove the system works over a multi-year
window) but they should never read as "live trades that lost real
money 18 months ago."

This module provides a single derived field, ``data_source``, that
the read APIs attach at response time. UI surfaces (paper-trades
table, prediction history) can then render a "Backtest" badge on
older rows without altering anything in the underlying collection.

Why response-time, not write-time
---------------------------------
- The cutover date is a deployment concern (when did this instance
  go public?), not a data concern.
- We can rotate the floor without rewriting historical docs.
- It works for legacy data already in the DB (the historic AAPL
  paper trades from before the public launch get labeled the
  moment we ship this — no migration).

Env knobs
---------
``PUBLIC_DATA_FLOOR_DATE`` — ISO date string (default ``2026-04-23``).
Anything with ``opened_at`` / ``predicted_at`` / ``created_at`` strictly
*before* this date is labeled ``backtest``; everything from that
date forward is ``live``.

The default is the user-provided cutover for RISEDUAL AI's public
launch. Operators changing it must update
``DEPLOYMENT_NOTES.md`` so future agents understand the meaning.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Iterable


_DEFAULT_FLOOR = "2026-04-23"


def _floor_dt() -> datetime:
    """Parse the floor env var into a UTC datetime. Falls back to
    the default on any malformed value (we never want a busted
    .env to crash a read endpoint)."""
    raw = (os.environ.get("PUBLIC_DATA_FLOOR_DATE") or _DEFAULT_FLOOR).strip()
    try:
        # Accept both "YYYY-MM-DD" and full ISO timestamps.
        if "T" in raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw + "T00:00:00+00:00")
    except ValueError:
        dt = datetime.fromisoformat(_DEFAULT_FLOOR + "T00:00:00+00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _coerce_dt(v: Any) -> datetime | None:
    """Best-effort coerce a Mongo doc timestamp into a UTC dt.
    Tolerates ISO strings, naive datetimes, and tz-aware datetimes.
    Returns ``None`` if the value is unparseable — caller defaults
    to ``"live"`` so unlabeled rows don't get falsely marked as
    backtest."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


# Document fields we'll consult, in priority order. The first
# non-None one wins. ``opened_at`` is for paper trades;
# ``predicted_at`` and ``created_at`` cover predictions and
# generic timeline rows.
_TIMESTAMP_FIELDS = ("opened_at", "predicted_at", "created_at", "timestamp")


def label_data_source(doc: dict | None) -> str:
    """Return ``"backtest"`` or ``"live"`` for a single document.
    Defaults to ``"live"`` on any missing/unparseable timestamp —
    we'd rather under-label than over-label legitimate live data."""
    if not isinstance(doc, dict):
        return "live"
    floor = _floor_dt()
    for key in _TIMESTAMP_FIELDS:
        ts = _coerce_dt(doc.get(key))
        if ts is not None:
            return "backtest" if ts < floor else "live"
    return "live"


def annotate(rows: Iterable[dict]) -> list[dict]:
    """Mutate a list of read-side docs to attach the ``data_source``
    field. Returns the same list for fluent chaining.

    Idempotent — calling twice doesn't change the result.
    """
    out: list[dict] = []
    for r in rows:
        if isinstance(r, dict):
            r["data_source"] = label_data_source(r)
        out.append(r)
    return out


def floor_date_iso() -> str:
    """Expose the active floor for UI tooltips ('Live data starts X')."""
    return _floor_dt().date().isoformat()
