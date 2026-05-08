"""
Symbol Failure Memory — short-term per-symbol penalty.

Closes the persistent-bias-loop hole that surfaced twice on NVDA: the
strategist keeps proposing the same direction even after a string of
losses, because past failures only flow into long-term ML training and
don't influence the *next* tick's conviction.

This service adds a short-term memory override that runs BEFORE Kelly
sizing. Two independent windows feed the penalty:

* **Last-N-trades window** (default N=5): count losses in the most recent
  N closed paper trades for this symbol/direction. Lossy here means a
  recent string of similar trades has been losing money.
* **7-day miss-count window**: count losses across the last 7 days.
  Catches slower-burn bias loops that span days.

Penalty schedule (tunable via env, defaults match operator spec):

| Recent-N losses | Penalty                      |
|-----------------|------------------------------|
| 0–1             | No effect                    |
| 2               | confidence × 0.7             |
| 3               | confidence × 0.5             |
| 4+              | force HOLD (return None)     |

| 7-day misses    | Penalty                      |
|-----------------|------------------------------|
| 0–1             | No effect                    |
| 2               | size_multiplier × 0.5        |
| 3+              | confidence capped at 0.75    |

Both windows compose — a symbol with 3 recent losses AND 4 in 7 days gets
both the confidence × 0.5 AND the size halved AND the 0.75 cap (the
tightest of the three wins). At 4+ recent losses the symbol is on a hard
cooldown until a non-loss trade clears the buffer.

The service is asset-agnostic: equity uses ``paper_trades``, crypto uses
``crypto_paper_trades``. Caller passes the appropriate collection name.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_RECENT_WINDOW: int = int(os.environ.get("SYMBOL_FAILURE_RECENT_N", "5"))
DEFAULT_LOOKBACK_DAYS: int = int(os.environ.get("SYMBOL_FAILURE_LOOKBACK_DAYS", "7"))


@dataclass(frozen=True)
class FailurePenalty:
    """Outcome of the penalty calculation."""

    recent_losses: int           # losses in last-N-trades window
    misses_7d: int               # losses in 7-day window
    confidence_multiplier: float
    size_multiplier: float
    confidence_cap: Optional[float]
    force_hold: bool
    reason: str

    def apply(self, *, confidence: float, size_multiplier: float) -> tuple[float, float]:
        """Apply the penalty to a (confidence, size_multiplier) pair.

        Returns the post-penalty values clamped to [0, 1].
        """
        if self.force_hold:
            return 0.0, 0.0
        c = confidence * self.confidence_multiplier
        if self.confidence_cap is not None:
            c = min(c, self.confidence_cap)
        s = size_multiplier * self.size_multiplier
        return max(0.0, min(1.0, c)), max(0.0, min(1.0, s))


def compute_failure_penalty(
    *,
    recent_losses: int,
    misses_7d: int,
) -> FailurePenalty:
    """Pure function — penalty calculation given the two miss counts.

    Tested independently of any Mongo I/O.
    """
    conf_mult = 1.0
    size_mult = 1.0
    conf_cap: Optional[float] = None
    force_hold = False
    reasons: list[str] = []

    # Recent-N branch
    if recent_losses >= 4:
        force_hold = True
        reasons.append(f"recent_losses>={recent_losses}_force_hold")
    elif recent_losses >= 3:
        conf_mult = min(conf_mult, 0.5)
        reasons.append("recent_losses>=3_conf*0.5")
    elif recent_losses >= 2:
        conf_mult = min(conf_mult, 0.7)
        reasons.append("recent_losses>=2_conf*0.7")

    # 7-day miss-count branch (composes with recent-N)
    if misses_7d >= 3:
        conf_cap = 0.75 if conf_cap is None else min(conf_cap, 0.75)
        reasons.append("misses_7d>=3_conf_cap_0.75")
    if misses_7d >= 2:
        size_mult = min(size_mult, 0.5)
        reasons.append("misses_7d>=2_size*0.5")

    if not reasons:
        reasons.append("no_recent_failures")

    return FailurePenalty(
        recent_losses=recent_losses,
        misses_7d=misses_7d,
        confidence_multiplier=conf_mult,
        size_multiplier=size_mult,
        confidence_cap=conf_cap,
        force_hold=force_hold,
        reason=";".join(reasons),
    )


async def _count_recent_losses(
    db: Any,
    *,
    symbol: str,
    coll_name: str,
    direction: Optional[str] = None,
    window: int = DEFAULT_RECENT_WINDOW,
) -> int:
    """Count losses in the last-N closed trades for ``symbol``.

    When ``direction`` is provided, only counts trades in the same
    direction (so a loss on a LONG doesn't penalize a fresh SHORT thesis).
    """
    if db is None:
        return 0
    try:
        match: dict[str, Any] = {
            "ticker" if coll_name == "paper_trades" else "symbol": symbol,
            "status": "closed",
            "outcome": {"$in": ["win", "loss", "flat"]},
        }
        if direction:
            match["direction"] = direction
        cur = db[coll_name].find(
            match,
            {"_id": 0, "outcome": 1, "closed_at": 1},
        ).sort("closed_at", -1).limit(window)
        rows = await cur.to_list(length=window)
        return sum(1 for r in rows if r.get("outcome") == "loss")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[symbol_failure] recent count failed for %s: %s", symbol, exc)
        return 0


async def _count_misses_7d(
    db: Any,
    *,
    symbol: str,
    coll_name: str,
    direction: Optional[str] = None,
    days: int = DEFAULT_LOOKBACK_DAYS,
) -> int:
    """Count losses in the last ``days`` for ``symbol`` (any direction by
    default — slower-burn bias detection)."""
    if db is None:
        return 0
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        match: dict[str, Any] = {
            "ticker" if coll_name == "paper_trades" else "symbol": symbol,
            "status": "closed",
            "outcome": "loss",
            "closed_at": {"$gte": cutoff},
        }
        if direction:
            match["direction"] = direction
        return await db[coll_name].count_documents(match)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[symbol_failure] 7d count failed for %s: %s", symbol, exc)
        return 0


async def get_failure_penalty(
    db: Any,
    *,
    symbol: str,
    direction: Optional[str] = None,
    asset_type: str = "equity",
) -> FailurePenalty:
    """Public entry — compute the penalty for a symbol/direction.

    Defaults to equity (``paper_trades``); pass ``asset_type="crypto"`` to
    target ``crypto_paper_trades``. ``direction`` filters the recent-N
    window to same-side trades; the 7-day window stays direction-agnostic
    so cross-direction failure clusters still get caught.
    """
    coll = "paper_trades" if asset_type == "equity" else "crypto_paper_trades"
    recent = await _count_recent_losses(
        db, symbol=symbol, coll_name=coll, direction=direction,
    )
    misses = await _count_misses_7d(
        db, symbol=symbol, coll_name=coll, direction=None,  # cross-direction
    )
    return compute_failure_penalty(recent_losses=recent, misses_7d=misses)
