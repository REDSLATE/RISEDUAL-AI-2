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


def to_iso_date(value: Any) -> Optional[str]:
    """Return canonical ``YYYY-MM-DD`` string or ``None``.

    Defensive companion to ``ensure_utc()`` for the Mongo→Chroma sync
    layer. Historic bug: callsites used ``mongo_doc.get("timestamp", "")[:10]``
    which silently failed in three different ways:

    * ``timestamp`` is a Mongo BSON ``datetime`` round-trip → slicing a
      ``datetime`` raises ``TypeError`` → swallowed by the broad except,
      Chroma row never written.
    * ``timestamp`` missing / ``None`` → ``""[:10]`` → empty string →
      Chroma stores a blank-date row that collides with all other
      blank-date rows in ``_make_id``'s v1 hash key.
    * ``timestamp`` non-ISO string (e.g. RFC 822) → ``[:10]`` returns
      garbage prefix → wrong date stored.

    This helper accepts:
    * ``datetime`` (any tz) → ``YYYY-MM-DD`` of the date portion
    * ``date`` → ISO-format
    * ``str`` ISO-8601 → first 10 chars *only after parse succeeds*
    * ``str`` looking like ``YYYY-MM-DD...`` → first 10 chars
    * Anything else → ``None``

    Returning ``None`` (not ``""``) so callers can ``if not date_str:
    skip`` cleanly without falling into the empty-string trap.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    # ``date`` (without time) — isinstance check has to come AFTER
    # datetime since ``datetime`` is a subclass of ``date``.
    try:
        from datetime import date as _date
        if isinstance(value, _date):
            return value.isoformat()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(value, str):
        if not value:
            return None
        # Try a real parse first — handles 'Z', offset, microseconds
        # without truncating in the wrong place.
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.date().isoformat()
        except ValueError:
            pass
        # Fallback: bare ``YYYY-MM-DD`` prefix. Strict — we require
        # exactly 10 chars matching the date shape, no slop.
        if len(value) >= 10 and value[4] == "-" and value[7] == "-":
            head = value[:10]
            try:
                datetime.strptime(head, "%Y-%m-%d")
                return head
            except ValueError:
                return None
        return None
    return None
