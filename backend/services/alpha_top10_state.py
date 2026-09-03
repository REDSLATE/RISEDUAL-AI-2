"""alpha_top10_state — shared in-process top-10 watchlist.

The Alpha Day Trader's 5-min tick ranks the wider universe (~49
candidates) down to a top slice. The 60s ``alpha_top10_stream``
tick uses that same slice as its watchlist so intents fire on
broker-fresh quotes instead of on 5-min-old snapshots.

Design notes
------------
* State lives in-process (single-worker deployment). A restart
  loses the list; the next 5-min tick refills it. That's fine —
  the 60s stream naturally degrades to "no work" when the
  watchlist is stale/empty, and the 5-min tick keeps running
  independently.
* Mongo mirror is best-effort (``alpha_top10_state`` collection,
  single-doc upsert keyed on ``_id="current"``). It exists so the
  operator can eyeball the watchlist from the diagnostic panel.
* We do NOT gate on pattern-match here — the top-10 is chosen
  purely by opportunity score, per the operator's requirement.
  Some entries will have no active setup yet; the stream tick
  gracefully skips them.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "symbols": [],          # list[str] — ordered by score desc
    "entries": [],          # list[dict] — {"symbol", "score", ...}
    "refreshed_at": None,   # datetime (UTC) or None
    "source_tick": None,    # opaque tick tag, e.g. "5min:2026-09-03T13:56:20Z"
}

MAX_TOP_N = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def set_top10(entries: list[dict[str, Any]], *, source_tick: Optional[str] = None) -> dict[str, Any]:
    """Replace the current watchlist. Truncates to ``MAX_TOP_N``.

    Each entry should carry at least ``symbol`` and ``score``.
    Extra fields (regime, family_tag, etc.) are preserved.
    """
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for e in entries or []:
        sym = str(e.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        cleaned.append({**e, "symbol": sym, "score": float(e.get("score") or 0.0)})
        if len(cleaned) >= MAX_TOP_N:
            break

    with _LOCK:
        _STATE["symbols"] = [e["symbol"] for e in cleaned]
        _STATE["entries"] = cleaned
        _STATE["refreshed_at"] = _now()
        _STATE["source_tick"] = source_tick
        snapshot = dict(_STATE)
    logger.info(
        "[alpha_top10] refreshed n=%d symbols=%s source=%s",
        len(cleaned), ",".join(e["symbol"] for e in cleaned) or "-",
        source_tick or "-",
    )
    return snapshot


def get_top10() -> dict[str, Any]:
    """Return a snapshot copy of the current watchlist state."""
    with _LOCK:
        return {
            "symbols": list(_STATE["symbols"]),
            "entries": [dict(e) for e in _STATE["entries"]],
            "refreshed_at": _STATE["refreshed_at"],
            "source_tick": _STATE["source_tick"],
        }


def clear() -> None:
    """Reset state — test helper."""
    with _LOCK:
        _STATE["symbols"] = []
        _STATE["entries"] = []
        _STATE["refreshed_at"] = None
        _STATE["source_tick"] = None


async def mirror_to_mongo(db: Any) -> None:
    """Best-effort Mongo mirror for the diagnostic panel. Never raises."""
    if db is None:
        return
    try:
        snap = get_top10()
        await db.alpha_top10_state.update_one(
            {"_id": "current"},
            {"$set": {
                "symbols": snap["symbols"],
                "entries": snap["entries"],
                "refreshed_at": snap["refreshed_at"],
                "source_tick": snap["source_tick"],
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_top10] mongo mirror failed (non-fatal): %s", exc)


__all__ = [
    "set_top10", "get_top10", "clear", "mirror_to_mongo", "MAX_TOP_N",
]
