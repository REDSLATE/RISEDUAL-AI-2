"""Datetime utilities — Mongo tz-aware safety.

MongoDB stores datetimes as BSON Dates which strip ``tzinfo`` and
truncate sub-millisecond precision on round-trip. Python then refuses
to subtract a naive datetime from a tz-aware ``datetime.now(timezone.utc)``
with::

    TypeError: can't subtract offset-naive and offset-aware datetimes

That error has historically been caught by broad ``except`` blocks and
silently dropped business logic (e.g., the auto-promotion suggestions).

``ensure_utc(dt)`` is the canonical guard: pipe any datetime read from
Mongo through this helper *before* using it in arithmetic or
comparisons against ``datetime.now(timezone.utc)``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def ensure_utc(dt: Any) -> Optional[datetime]:
    """Return a tz-aware UTC datetime or ``None``.

    * ``datetime`` with tzinfo  → returned unchanged.
    * ``datetime`` without tzinfo → tagged as UTC (the assumption used
      everywhere in this codebase; we always *write* UTC into Mongo).
    * ``str`` (ISO-8601, with or without trailing ``Z``) → parsed and
      tagged UTC if naive. Returns ``None`` on parse failure rather
      than raising — callers decide whether missing data is fatal.
    * Anything else (including ``None``) → ``None``.
    """
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    if isinstance(dt, str):
        try:
            parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None
