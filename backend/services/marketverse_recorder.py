"""MarketVerse observation recorder — Alpha shadow-layer hook.

Phase 0 entry point (2026-06-01): wires ``services.rise_marketverse``
into Alpha's shadow layer without touching the live decision loop.
Writes observations to ``marketverse_observations`` (NEW collection,
isolated from the existing ``research_shadow_decisions`` scorer
surface).

Doctrine
--------
* SHADOW ONLY — no live consumption, no broker authority.
* Pure observation — every call is read-bars-and-record. Never
  feeds anything back into the active trade path in this phase.
* Defensive — any exception is swallowed (logged warn). MarketVerse
  must NEVER take down the live bot.

Lives in its own module (rather than inside
``research_shadow_engines.py``) so the engines file stays under the
800-line code-size ceiling and the marketverse hook is cleanly
separable for v0.2.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _utcnow_naive():
    """Naive UTC ``datetime`` for Mongo storage, mirroring the
    existing shadow-decisions ``ts`` convention."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def record_marketverse_observation(
    db: Any, *, symbol: str, bars: list, asset_type: str = "crypto",
    horizon_bars: int = 10, active_action: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Optional[str]:
    """Run the MarketVerse panel against ``bars`` and persist a row
    to ``marketverse_observations``. Returns the inserted ``_id``
    (str-ified) on success, ``None`` if the panel was skipped or
    Mongo failed.

    Read-only on the rest of Alpha:
      * does NOT modify ``research_shadow_decisions``
      * does NOT call any brain in the live decision path
      * does NOT raise — exceptions are logged + swallowed

    Args:
        symbol: instrument symbol (e.g. ``"BTC/USD"``).
        bars: OHLCV bars, oldest-first; each bar a Mapping with
            at minimum a numeric ``c`` (close) field.
        asset_type: ``"crypto"`` / ``"stock"`` / ``"options"`` —
            kept for downstream filtering / scorer bucketing.
        horizon_bars: how far the path projection looks ahead.
        active_action: optional snapshot of what the LIVE brain
            decided this same cycle, so a future scorer can grade
            shadow-vs-active disagreement without joining tables.
        extra: optional dict of arbitrary metadata to stash on the
            row (regime tag from upstream, trace_id, etc.).
    """
    if db is None or not symbol or not bars:
        return None
    try:
        from services.rise_marketverse import (
            MarketWorldModel,
            ShadowBrainArena,
            compute_disagreement,
            marketstate_asdict,
            opinions_asdict,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[marketverse] module import failed: %s", exc)
        return None

    try:
        world_model = MarketWorldModel(horizon_bars=horizon_bars)
        arena = ShadowBrainArena()
        state = world_model.infer_state(bars, symbol=symbol)
        opinions = arena.decide_all(state)
        spread = compute_disagreement(opinions)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[marketverse] panel computation failed symbol=%s err=%s",
            symbol, exc,
        )
        return None

    row: dict[str, Any] = {
        "symbol": symbol,
        "asset_type": asset_type,
        "market_state": marketstate_asdict(state),
        "shadow_outputs": opinions_asdict(opinions),
        "disagreement": spread,
        "active_action": active_action,
        "ts": _utcnow_naive(),
    }
    if extra:
        # Caller-supplied metadata rides under a namespaced key so
        # it can't collide with the canonical fields above.
        row["meta"] = dict(extra)

    try:
        result = await db.marketverse_observations.insert_one(row)
        inserted_id = getattr(result, "inserted_id", None)
        return str(inserted_id) if inserted_id is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[marketverse] mongo insert failed symbol=%s err=%s",
            symbol, exc,
        )
        return None


__all__ = ["record_marketverse_observation"]
