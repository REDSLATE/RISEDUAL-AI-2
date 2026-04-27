"""Mongo writer + ensure-indexes for ``research_shadow_decisions``.

Mirrors the architectural pattern of :mod:`services.adversarial_logger`
deliberately — same write-then-patch lifecycle, same defensive
try/except discipline (a shadow logging failure must NEVER block an
active trade).

Tier-3 firewall
---------------
This module is the ONLY allowed writer to ``research_shadow_decisions``.
It explicitly does NOT:

* touch ``paper_trades`` / ``crypto_paper_trades``
* touch ``prediction_tracker``
* touch ``trading_bots[].stats``

If you find yourself wanting to cross those boundaries, stop and
revisit the design — the firewall is what keeps Tier-3 honest.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.research_shadow import ShadowDecision

logger = logging.getLogger(__name__)

SHADOW_COLLECTION = "research_shadow_decisions"


async def ensure_indexes(db: Any) -> None:
    """Idempotent index creation. Best-effort — Mongo will reject a
    duplicate index gracefully, so re-running is safe.
    """
    if db is None:
        return
    try:
        coll = db[SHADOW_COLLECTION]
        await coll.create_index([("ts", -1)], name="ts_desc")
        await coll.create_index(
            [("bot_id", 1), ("ts", -1)], name="bot_ts_desc",
        )
        await coll.create_index(
            [("is_dissent", 1), ("pending_counterfactual", 1), ("ts", 1)],
            name="dissent_pending_ts",
        )
        await coll.create_index(
            [("decision_id", 1)], unique=True, name="decision_id_uniq",
        )
        await coll.create_index(
            [("symbol", 1), ("ts", -1)], name="symbol_ts_desc",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research-shadow] ensure_indexes failed: %s", exc)


async def insert_shadow_decision(
    db: Any, decision: ShadowDecision,
) -> Optional[str]:
    """Persist a shadow decision. Returns the ``decision_id`` on
    success, ``None`` on any failure.

    Failure semantics: returning None is fine — the active trade
    path doesn't need this id for anything critical. The caller
    should treat insert failures as observability gaps, not
    correctness gaps.
    """
    if db is None:
        return None
    try:
        await db[SHADOW_COLLECTION].insert_one(decision.to_doc())
        return decision.decision_id
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[research-shadow] insert failed for %s/%s: %s",
            decision.bot_id, decision.symbol, exc,
        )
        return None


async def patch_scores(
    db: Any,
    decision_id: str,
    *,
    tactical_score: Optional[dict[str, Any]] = None,
    strategic_score: Optional[dict[str, Any]] = None,
) -> bool:
    """Patch tactical and/or strategic scores onto a shadow row.

    Idempotent: re-applying the same scores is a no-op. Only flips
    ``pending_counterfactual`` to False once BOTH score buckets are
    populated (or the shadow's strategic score is structurally
    inapplicable — see scorer for that logic).
    """
    if db is None or not decision_id:
        return False

    update: dict[str, Any] = {}
    if tactical_score is not None:
        update["tactical_score"] = tactical_score
    if strategic_score is not None:
        update["strategic_score"] = strategic_score

    if not update:
        return False

    try:
        await db[SHADOW_COLLECTION].update_one(
            {"decision_id": decision_id},
            {"$set": update},
        )
        # Re-read the row to decide whether to flip the pending flag.
        # Cheap (indexed by decision_id) and avoids a race with a
        # second worker patching the other score type.
        row = await db[SHADOW_COLLECTION].find_one(
            {"decision_id": decision_id},
            {"_id": 0, "tactical_score": 1, "strategic_score": 1,
             "shadow_action": 1, "active_action": 1, "is_dissent": 1},
        )
        if row is None:
            return True

        # A row is "fully scored" when:
        #  - tactical_score is present, AND
        #  - strategic_score is present OR structurally not applicable.
        # Strategic only applies to dissents where shadow said HOLD —
        # the "would shadow have ridden the winner longer?" case.
        # Other dissents have only tactical scoring.
        from services.research_shadow import canonicalise_action
        shadow_says_hold = canonicalise_action(row.get("shadow_action")) == "HOLD"
        is_dissent = bool(row.get("is_dissent"))
        strategic_applicable = is_dissent and shadow_says_hold

        has_tactical = row.get("tactical_score") is not None
        has_strategic = row.get("strategic_score") is not None

        fully_scored = has_tactical and (has_strategic or not strategic_applicable)
        if fully_scored:
            await db[SHADOW_COLLECTION].update_one(
                {"decision_id": decision_id},
                {"$set": {"pending_counterfactual": False}},
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[research-shadow] patch_scores failed for %s: %s", decision_id, exc,
        )
        return False


async def get_daily_cost_usd(db: Any, bot_id: str) -> float:
    """Sum LLM spend on shadow rows for this bot in the rolling 24h
    window. Used by the gate to enforce the daily ceiling.
    """
    if db is None or not bot_id:
        return 0.0
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        cursor = db[SHADOW_COLLECTION].aggregate([
            {"$match": {"bot_id": bot_id, "ts": {"$gte": cutoff}}},
            {"$group": {"_id": None, "total": {"$sum": "$llm_cost_usd"}}},
        ])
        rows = await cursor.to_list(length=1)
        if not rows:
            return 0.0
        return float(rows[0].get("total") or 0.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[research-shadow] get_daily_cost_usd failed for %s: %s", bot_id, exc,
        )
        return 0.0


async def get_last_shadow_ts(db: Any, bot_id: str) -> Optional[datetime]:
    """Return the timestamp of the most recent shadow row for this
    bot, or None. Used by the rate-limit gate.
    """
    if db is None or not bot_id:
        return None
    try:
        row = await db[SHADOW_COLLECTION].find_one(
            {"bot_id": bot_id},
            {"_id": 0, "ts": 1},
            sort=[("ts", -1)],
        )
        if not row:
            return None
        ts = row.get("ts")
        if isinstance(ts, datetime):
            # Treat naive datetimes as UTC (Mongo can return naive).
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[research-shadow] get_last_shadow_ts failed for %s: %s", bot_id, exc,
        )
        return None


async def count_recent_agreement_run(
    db: Any, bot_id: str, max_lookback: int = 50,
) -> int:
    """Count the number of consecutive most-recent shadow rows for
    this bot that were NOT dissents (agreement runs).

    Walks backwards from the newest row, stopping at the first
    dissent (or after ``max_lookback`` rows — defence against an
    accidentally-paused dissent never resetting the counter).

    Used by the disagreement-triggered cycle frequency gate. Cheap
    Mongo query — uses the ``bot_ts_desc`` compound index.
    """
    if db is None or not bot_id:
        return 0
    try:
        rows = await db[SHADOW_COLLECTION].find(
            {"bot_id": bot_id},
            {"_id": 0, "is_dissent": 1, "decision_phase": 1},
        ).sort("ts", -1).limit(max_lookback).to_list(length=max_lookback)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[research-shadow] count_recent_agreement_run failed for %s: %s",
            bot_id, exc,
        )
        return 0

    run = 0
    for row in rows:
        # Only cycle-phase rows count toward the agreement run —
        # entry/exit dissents shouldn't reset the counter for cycle
        # skipping (they're separate signals).
        if row.get("decision_phase") != "cycle":
            continue
        if row.get("is_dissent"):
            break
        run += 1
    return run
