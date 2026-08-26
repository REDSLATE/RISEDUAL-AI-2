"""Council Consultation — cross-brain read + position-size modulator.

Reads recent brain contributions/opinions from the local in-process
brains (sovereign now; adversarial + shadow when their equity paths
land) and derives a **size modulator** in ``[0.0, 1.0]``. The modulator
can only SHRINK Alpha's position, never grow it, never veto direction.
Council never blocks a trade.

Doctrine (see /app/docs/POSTMORTEM_ACCOUNT_AWARE_OVERLAY.md):
* **Shadow always.** Every call logs what modulation WOULD have done,
  regardless of the enforce flag.
* **Kill switch.** ``RISEDUAL_COUNCIL_MODULATE_ENABLED`` default OFF.
  When OFF, the returned modulator is 1.0 (no size change) but the
  shadow decision is still recorded.
* **Read-only for the trade path.** Consultation must not raise, must
  not exceed a millisecond of blocking work, and its DB reads are
  bounded (last 6h, capped result count).
* **Size floor.** The modulator's minimum output is 0.25 — the
  council can cut Alpha's size to a quarter but never to zero.

The output shape::

    {
        "modulator": 0.5,       # multiplier in [0.25, 1.0]
        "enforced": False,      # true iff env flag is on
        "consensus": "up"|"down"|"flat"|"unknown",
        "dissent_ratio": 0.33,  # 0 = full agreement, 1 = all disagree
        "votes": [{"brain": "sovereign", "direction": "up", ...}, ...],
        "computed_at": "iso",
    }
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

ENV_ENFORCE = "RISEDUAL_COUNCIL_MODULATE_ENABLED"

MIN_MODULATOR = 0.25
LOG_COLLECTION = "council_consultation_log"


def modulate_enabled() -> bool:
    """Master enforce flag. Default OFF — council modulation is
    shadow-only until the operator flips this."""
    raw = (os.environ.get(ENV_ENFORCE) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _norm_direction(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).lower()
    if s in {"up", "long", "buy", "bullish", "strong_buy", "weak_buy"}:
        return "up"
    if s in {"down", "short", "sell", "bearish", "strong_sell", "weak_sell"}:
        return "down"
    if s in {"flat", "hold", "neutral"}:
        return "flat"
    return None


async def _collect_sovereign_votes(db: Any, symbol: str) -> list[dict[str, Any]]:
    """Read recent sovereign contributions for the symbol from
    ``mc2_contributions`` (local MC2 substitute)."""
    if db is None:
        return []
    since = datetime.now(timezone.utc) - timedelta(hours=6)
    votes: list[dict[str, Any]] = []
    try:
        cursor = db.mc2_contributions.find(
            {
                "symbol": symbol.upper(),
                "recorded_at_dt": {"$gte": since},
            },
            {"_id": 0, "brain": 1, "direction": 1, "confidence": 1,
             "recorded_at": 1},
        ).sort("recorded_at_dt", -1).limit(10)
        async for row in cursor:
            direction = _norm_direction(row.get("direction"))
            if direction is None:
                continue
            votes.append({
                "brain": row.get("brain") or "sovereign",
                "direction": direction,
                "confidence": float(row.get("confidence") or 0.0),
                "at": row.get("recorded_at"),
            })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[council] sovereign read failed (non-fatal): %s", exc)
    return votes


async def _collect_multi_model_votes(db: Any, symbol: str) -> list[dict[str, Any]]:
    """Read latest multi-model hypothesis for the symbol, if present.

    Uses the ``multi_model_hypotheses`` collection if the service has
    written one recently. Silent on absence.
    """
    if db is None:
        return []
    since = datetime.now(timezone.utc) - timedelta(hours=6)
    votes: list[dict[str, Any]] = []
    try:
        row = await db.multi_model_hypotheses.find_one(
            {"symbol": symbol.upper(), "created_at_dt": {"$gte": since}},
            sort=[("created_at_dt", -1)],
        )
        if not row:
            return []
        # The service stores per-brain sub-hypotheses.
        for sub in row.get("brain_hypotheses") or []:
            direction = _norm_direction(sub.get("direction"))
            if direction is None:
                continue
            votes.append({
                "brain": sub.get("brain") or "unknown",
                "direction": direction,
                "confidence": float(sub.get("confidence") or 0.0),
                "at": row.get("created_at"),
            })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[council] multi-model read failed (non-fatal): %s", exc)
    return votes


def _derive_modulator(
    alpha_direction: str,
    votes: list[dict[str, Any]],
) -> tuple[float, str, float]:
    """Pure: return ``(modulator, consensus, dissent_ratio)``.

    ``modulator`` in ``[MIN_MODULATOR, 1.0]``. Only shrinks. Never
    veto, never grow.
    """
    if not votes:
        # No opinions to consult — no modulation.
        return (1.0, "unknown", 0.0)

    a_dir = _norm_direction(alpha_direction) or "unknown"

    # Tally agreement weighted by confidence.
    agree = 0.0
    disagree = 0.0
    up = 0.0
    down = 0.0
    for v in votes:
        w = max(0.05, min(1.0, float(v.get("confidence") or 0.5)))
        if v["direction"] == a_dir:
            agree += w
        elif v["direction"] in {"up", "down"} and a_dir in {"up", "down"} and v["direction"] != a_dir:
            disagree += w
        # "flat" votes count softly against action.
        elif v["direction"] == "flat":
            disagree += w * 0.5
        if v["direction"] == "up":
            up += w
        elif v["direction"] == "down":
            down += w

    total = agree + disagree
    dissent_ratio = disagree / total if total > 0 else 0.0

    if up > down:
        consensus = "up"
    elif down > up:
        consensus = "down"
    else:
        consensus = "flat"

    # Piecewise modulator: agreement = full size; every 20% dissent
    # shaves 15% off, floored at MIN_MODULATOR.
    if dissent_ratio <= 0.2:
        modulator = 1.0
    elif dissent_ratio <= 0.4:
        modulator = 0.85
    elif dissent_ratio <= 0.6:
        modulator = 0.6
    elif dissent_ratio <= 0.8:
        modulator = 0.4
    else:
        modulator = MIN_MODULATOR

    return (round(modulator, 3), consensus, round(dissent_ratio, 3))


async def consult_council(
    db: Any,
    *,
    symbol: str,
    alpha_direction: str,
    alpha_confidence: float | None = None,
    strategy_id: str | None = None,
) -> dict[str, Any]:
    """Return the council's read for a single Alpha decision.

    NEVER raises. Bounded work (≤ 20 ms typical). Read-only. When
    the enforce flag is off, ``modulator`` is forced to 1.0 but the
    derived ``shadow_modulator`` still carries what it WOULD have
    been — for post-hoc analysis via the log collection.
    """
    t0 = time.time_ns()
    try:
        votes = (
            await _collect_sovereign_votes(db, symbol)
            + await _collect_multi_model_votes(db, symbol)
        )
        shadow_modulator, consensus, dissent = _derive_modulator(
            alpha_direction, votes,
        )
        enforced = modulate_enabled()
        modulator = shadow_modulator if enforced else 1.0
        result = {
            "modulator": modulator,
            "shadow_modulator": shadow_modulator,
            "enforced": enforced,
            "consensus": consensus,
            "dissent_ratio": dissent,
            "votes": votes,
            "vote_count": len(votes),
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": (time.time_ns() - t0) / 1_000_000,
        }
        # Fire-and-forget log (best-effort, must not block the trade).
        try:
            await db[LOG_COLLECTION].insert_one({
                "symbol": symbol.upper(),
                "alpha_direction": alpha_direction,
                "alpha_confidence": alpha_confidence,
                "strategy_id": strategy_id,
                **result,
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("[council] log insert failed (non-fatal): %s", exc)
        return result
    except Exception as exc:  # noqa: BLE001
        # Any failure ⇒ neutral modulator, empty verdict. Trade proceeds.
        logger.debug("[council] consult failed (non-fatal): %s", exc)
        return {
            "modulator": 1.0,
            "shadow_modulator": 1.0,
            "enforced": modulate_enabled(),
            "consensus": "unknown",
            "dissent_ratio": 0.0,
            "votes": [],
            "vote_count": 0,
            "error": str(exc),
        }


__all__ = [
    "ENV_ENFORCE",
    "LOG_COLLECTION",
    "MIN_MODULATOR",
    "consult_council",
    "modulate_enabled",
    "_derive_modulator",  # exported for tests
]
