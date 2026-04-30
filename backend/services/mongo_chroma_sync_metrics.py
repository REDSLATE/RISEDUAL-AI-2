"""Mongo→Chroma sync observability.

Lightweight in-process counters + last-rebuild timestamp. Exposed
through the admin drift detector endpoint so the operator can see:

  * How many save attempts have been skipped, broken down by reason
  * When the last full rebuild ran (so 8% drift one hour after a
    rebuild reads as "actively broken" instead of "expected")

Why in-process and not Prometheus / Mongo:
  * Counters reset on restart — that's fine; they're an operational
    smoke signal, not a long-term metric. The drift detector itself
    measures absolute state (Mongo count vs Chroma count) so it
    catches regressions even after a restart.
  * One less moving part. The drift endpoint reads these directly.

The ``record_skip(reason)`` helper is callable from anywhere on the
sync path — every site that previously had ``except Exception:
skipped += 1`` now logs at WARN and bumps the counter, so the next
"ChromaDB drifted from MongoDB" incident shows up in observability
instead of in a screenshot four months later.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── In-process counters ─────────────────────────────────────────────

_skipped_total: dict[str, int] = {}
_last_rebuild_at: Optional[datetime] = None
_last_rebuild_summary: Optional[dict] = None


def record_skip(reason: str, *, doc_id: Optional[str] = None,
                exc: Optional[BaseException] = None) -> None:
    """Bump the skip counter and log at WARN.

    ``reason`` is a short stable label — keep the cardinality low
    (handful of values) so the counter remains useful. Examples:
    ``"chroma_upsert_failed"``, ``"missing_symbol"``,
    ``"date_coerce_failed"``.
    """
    _skipped_total[reason] = _skipped_total.get(reason, 0) + 1
    logger.warning(
        "[mongo_chroma_sync] skipped reason=%s doc_id=%s exc=%s/%s",
        reason,
        (doc_id or "")[:32],
        type(exc).__name__ if exc else "-",
        (str(exc)[:120] if exc else "-"),
    )


def get_skip_counters() -> dict[str, int]:
    """Read-only snapshot for the drift endpoint."""
    return dict(_skipped_total)


def reset_counters() -> None:
    """Reset counters — used by tests; not exposed via HTTP."""
    _skipped_total.clear()


# ── Last-rebuild bookkeeping ────────────────────────────────────────


def mark_rebuild(*, rebuilt: int, skipped: int, since: Optional[str] = None) -> None:
    """Stamp the last-rebuild metadata. Called by both
    ``server.py``'s startup warmup and the
    ``/memory/rebuild-from-mongo`` admin endpoint, so the drift
    detector can answer "8% drift, but we just rebuilt" vs "8%
    drift one hour after rebuild → actively broken"."""
    global _last_rebuild_at, _last_rebuild_summary
    _last_rebuild_at = datetime.now(timezone.utc)
    _last_rebuild_summary = {
        "rebuilt": int(rebuilt),
        "skipped": int(skipped),
        "since": since,
    }


def get_last_rebuild() -> dict[str, Any]:
    return {
        "last_rebuild_at": (
            _last_rebuild_at.isoformat() if _last_rebuild_at else None
        ),
        "last_rebuild_summary": dict(_last_rebuild_summary)
        if _last_rebuild_summary else None,
    }
