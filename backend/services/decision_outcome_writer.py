"""
Decision Outcome Writer — Stage 3 ground-truth backfill.

When a paper trade closes, this module finds the matching
``decision_pairs`` row by ``trade_id`` and stamps the realized
outcome onto it (pnl_usd, pnl_pct, outcome label) along with a
``scoreboard`` summarising which voice was right.

Scoring is intentionally simple:

* The trade's realized direction is LONG if pnl_usd > 0 and we
  were already LONG, etc. We don't second-guess direction — the
  paper trade tells us directly via its ``direction`` field and
  the ``outcome`` ("win"/"loss"/"flat") set by the closer.
* Each voice was "right" when its **action** matched the trade's
  direction AND the trade was a win, OR when its action was HOLD
  and the trade was a flat/loss. (HOLD is the conservative bet;
  it's neither right nor wrong on a flat outcome.)
* The scoreboard exposes:
    - ``sovereign_correct``: bool
    - ``council_correct``: bool
    - ``winner``: "sovereign" | "council" | "tie" | "neither"

This is the ONE place that decides who-was-right semantics.
Centralising it keeps the dashboard / stats route honest.

Doctrine
--------
* This module STAMPS; it never DECIDES. The pair was already filed
  with both verdicts; we only attach the realised outcome.
* No network calls. No LLM. Pure DB writes.
* Best-effort: a missing pair (the close beat the file) returns
  ``{"ok": False, "reason": "pair_not_found"}`` instead of raising.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from services.intent_decision_filer import COLLECTION as PAIRS_COLLECTION

logger = logging.getLogger(__name__)


def _score_voice(action: str, trade_direction: str,
                 outcome_label: str) -> bool:
    """Return True if this voice's action was vindicated by the
    realised trade outcome.

    Rules:
    * LONG voice + LONG trade + win → correct
    * SHORT voice + SHORT trade + win → correct
    * Voice action opposite to trade direction + loss → correct
      (i.e. the voice would have said HOLD or the other side, and
      the trade lost. We don't have the counterfactual here, so we
      conservatively only credit voices whose action matched a
      winning trade.)
    * HOLD voice + flat outcome → correct (HOLD got the no-action right).
    * Anything else → incorrect.

    All direction-token normalisation routes through
    ``services.prediction_tracker.canonical_ai_dir`` so we never carry
    a private alias table that could drift from the centralised one.
    """
    from services.prediction_tracker import canonical_ai_dir

    a_raw = (action or "").upper().strip()
    o = (outcome_label or "").lower()

    if a_raw == "HOLD":
        # HOLD vindicated only when the trade ended flat or losing.
        # A HOLD that watched the trade win is NOT credited.
        return o in ("flat", "loss")

    voice_side = canonical_ai_dir(action)
    trade_side = canonical_ai_dir(trade_direction)

    if voice_side == "UNKNOWN" or trade_side == "UNKNOWN":
        return False

    if voice_side == trade_side:
        return o == "win"
    # Voice argued the opposite side of the trade. Crediting the
    # voice only when the trade actually lost (so the opposite side
    # would have been correct).
    return o == "loss"


def _decide_winner(sov_correct: bool, council_correct: bool,
                   council_action: str) -> str:
    """Top-level scoreboard label. Treats an ABSENT council as a
    one-sided round Sovereign can win on its own."""
    council_present = (council_action or "").upper() not in ("UNKNOWN", "ABSENT", "")
    if not council_present:
        return "sovereign" if sov_correct else "neither"
    if sov_correct and council_correct:
        return "tie"
    if sov_correct:
        return "sovereign"
    if council_correct:
        return "council"
    return "neither"


async def write_outcome_for_trade(
    db: Any, *,
    trade_id: str,
    direction: str,
    pnl_usd: float,
    pnl_pct: float,
    outcome_label: str,
    closed_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Stamp the realised outcome onto the matching pair.

    Returns ``{"ok": True, "decision_id": ...}`` on success or
    ``{"ok": False, "reason": ...}`` on no-match.
    """
    if not trade_id:
        return {"ok": False, "reason": "missing_trade_id"}

    pair = await db[PAIRS_COLLECTION].find_one(
        {"trade_id": trade_id}, {"_id": 0},
    )
    if not pair:
        return {"ok": False, "reason": "pair_not_found"}

    sov_action = (pair.get("sovereign") or {}).get("action") or "HOLD"
    council_action = (pair.get("council") or {}).get("action") or "UNKNOWN"

    sov_correct = _score_voice(sov_action, direction, outcome_label)
    council_correct = _score_voice(council_action, direction, outcome_label)
    winner = _decide_winner(sov_correct, council_correct, council_action)

    outcome_doc = {
        "trade_direction": (direction or "").upper(),
        "pnl_usd": float(pnl_usd),
        "pnl_pct": float(pnl_pct),
        "outcome_label": (outcome_label or "").lower(),
        "scoreboard": {
            "sovereign_correct": sov_correct,
            "council_correct": council_correct,
            "winner": winner,
        },
        "resolved_at": closed_at or datetime.now(timezone.utc),
    }

    await db[PAIRS_COLLECTION].update_one(
        {"decision_id": pair["decision_id"]},
        {"$set": {
            "resolved": True,
            "outcome": outcome_doc,
        }},
    )
    logger.info(
        "OUTCOME_FILED trade_id=%s symbol=%s winner=%s sov_correct=%s "
        "council_correct=%s pnl=%.2f",
        trade_id, pair.get("symbol"), winner,
        sov_correct, council_correct, float(pnl_usd),
    )
    return {
        "ok": True,
        "decision_id": pair["decision_id"],
        "winner": winner,
        "sovereign_correct": sov_correct,
        "council_correct": council_correct,
    }


async def attach_council_verdict(
    db: Any, *,
    symbol: str,
    council_hypothesis: Mapping[str, Any],
    window_seconds: int = 600,
) -> dict[str, Any]:
    """If an UNRESOLVED pair exists for this symbol within the last
    ``window_seconds`` seconds AND its council voice is still
    ABSENT, attach the council voice to it.

    This is the bridge between the (live) sovereign tick and the
    (on-demand) council hypothesis: a hypothesis generated within
    ~10 min of a sovereign decision for the same symbol is treated
    as the council's vote on that decision.

    Best-effort. Multiple calls are idempotent — once a pair has a
    non-ABSENT council voice, this function skips it.
    """
    from services.intent_decision_filer import _council_voice_from_hypothesis

    sym = (symbol or "").upper()
    if not sym:
        return {"ok": False, "reason": "missing_symbol"}
    cutoff = datetime.now(timezone.utc).timestamp() - max(60, int(window_seconds))
    # Find the most recent absent-council unresolved pair for this symbol.
    cursor = db[PAIRS_COLLECTION].find(
        {
            "symbol": sym,
            "resolved": False,
            "council.action": {"$in": ["UNKNOWN", "ABSENT", None]},
        },
        {"_id": 0},
    ).sort("created_at", -1).limit(5)
    rows = []
    async for r in cursor:
        rows.append(r)
    if not rows:
        return {"ok": False, "reason": "no_open_pair"}

    target = None
    for r in rows:
        created = r.get("created_at")
        if isinstance(created, datetime):
            ts = created.timestamp()
        else:
            continue
        if ts >= cutoff:
            target = r
            break
    if target is None:
        return {"ok": False, "reason": "no_pair_in_window"}

    council_voice = _council_voice_from_hypothesis(council_hypothesis)
    # Recompute agreement now that council is known.
    sov_action = (target.get("sovereign") or {}).get("action") or "HOLD"
    c_action = council_voice["action"]
    if sov_action == c_action:
        agreement = "AGREE"
    elif sov_action == "HOLD" or c_action == "HOLD":
        agreement = "PARTIAL"
    else:
        agreement = "DISAGREE"

    await db[PAIRS_COLLECTION].update_one(
        {"decision_id": target["decision_id"]},
        {"$set": {"council": council_voice, "agreement": agreement}},
    )
    logger.info(
        "COUNCIL_ATTACHED decision_id=%s symbol=%s council=%s agreement=%s",
        target["decision_id"], sym, council_voice["verdict"], agreement,
    )
    return {
        "ok": True,
        "decision_id": target["decision_id"],
        "agreement": agreement,
    }


async def aggregate_stats(
    db: Any, *, lane: Optional[str] = None,
    since_days: int = 30,
) -> dict[str, Any]:
    """Top-level Stage 3 dashboard stats.

    Returns counts of correct / incorrect / unresolved by voice plus
    an agreement breakdown."""
    q: dict[str, Any] = {}
    if lane:
        q["lane"] = lane
    if since_days and since_days > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400
        q["created_at"] = {"$gte": datetime.fromtimestamp(cutoff, tz=timezone.utc)}

    total = await db[PAIRS_COLLECTION].count_documents(q)
    resolved_q = {**q, "resolved": True}
    resolved = await db[PAIRS_COLLECTION].count_documents(resolved_q)

    sov_correct = await db[PAIRS_COLLECTION].count_documents(
        {**resolved_q, "outcome.scoreboard.sovereign_correct": True},
    )
    council_correct = await db[PAIRS_COLLECTION].count_documents(
        {**resolved_q, "outcome.scoreboard.council_correct": True},
    )
    agree = await db[PAIRS_COLLECTION].count_documents({**q, "agreement": "AGREE"})
    disagree = await db[PAIRS_COLLECTION].count_documents({**q, "agreement": "DISAGREE"})
    partial = await db[PAIRS_COLLECTION].count_documents({**q, "agreement": "PARTIAL"})

    # Winner breakdown
    winners = {"sovereign": 0, "council": 0, "tie": 0, "neither": 0}
    for w in winners:
        winners[w] = await db[PAIRS_COLLECTION].count_documents(
            {**resolved_q, "outcome.scoreboard.winner": w},
        )

    def _rate(num: int, denom: int) -> float:
        return round(num / denom, 4) if denom else 0.0

    return {
        "total_pairs": total,
        "resolved": resolved,
        "unresolved": total - resolved,
        "sovereign": {
            "correct": sov_correct,
            "incorrect": resolved - sov_correct,
            "accuracy": _rate(sov_correct, resolved),
        },
        "council": {
            "correct": council_correct,
            "incorrect": resolved - council_correct,
            "accuracy": _rate(council_correct, resolved),
        },
        "agreement": {
            "agree": agree,
            "partial": partial,
            "disagree": disagree,
            "agree_rate": _rate(agree, total),
        },
        "winners": winners,
        "since_days": since_days,
        "lane": lane,
    }


__all__ = [
    "write_outcome_for_trade",
    "attach_council_verdict",
    "aggregate_stats",
    "_score_voice",
    "_decide_winner",
]
