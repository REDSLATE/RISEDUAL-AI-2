"""
Intent Decision Filer — pairs Sovereign + Council verdicts.

Every Sovereign tick produces a Sovereign verdict. The 4-LLM council
produces its own verdict (in this same stack, via
``multi_model_hypothesis_service``). This module pairs them on a
single Mongo doc in the ``decision_pairs`` collection so the
operator can read them side-by-side and so the outcome writer can
backfill realized PnL once the position closes.

This is the **filing layer** of the Stage 3 evidence pipeline:

    Sovereign decides   →   render_voice   ─┐
                                            ├─►  decision_pairs row
    Council decides     →   council voice  ─┘            │
                                                         │
                            (position closes some days later)
                                                         │
                            decision_outcome_writer  ←───┘
                            (backfills realized PnL)

Doctrine
--------
* This module STORES; it never DECIDES. Both verdicts are already
  determined upstream — we just pair and persist.
* No LLM calls here. The council's voice is the council's existing
  hypothesis text; the sovereign's voice is the ``sovereign_voice``
  module's deterministic render.
* MongoDB ``_id`` is always excluded on reads (BSON safety doctrine).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from services.sovereign_voice import render_voice

logger = logging.getLogger(__name__)


COLLECTION = "decision_pairs"


def _council_voice_from_hypothesis(
    hypothesis: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Extract a stable shape from whatever the council's hypothesis
    dict looks like. The council's verdict + summary already exist;
    we just normalize the keys."""
    if not hypothesis:
        return {
            "verdict": "ABSENT",
            "action": "UNKNOWN",
            "confidence": 0.0,
            "rationale_text": "Council did not produce a verdict for this tick.",
        }
    action = (hypothesis.get("display_action") or hypothesis.get("verdict")
              or hypothesis.get("market_decision") or "HOLD")
    raw_conf = hypothesis.get("final_confidence", hypothesis.get("confidence", 0))
    try:
        conf = float(raw_conf) / 100.0 if raw_conf and raw_conf > 1 else float(raw_conf)
    except (TypeError, ValueError):
        conf = 0.0
    rationale = (hypothesis.get("summary") or hypothesis.get("rationale") or "")[:1500]
    return {
        "verdict": f"{str(action).upper()} {conf:.2f}",
        "action": str(action).upper(),
        "confidence": round(conf, 4),
        "rationale_text": rationale,
        "council_penalty": hypothesis.get("council_penalty"),
        "execution_decision": hypothesis.get("execution_decision"),
        "high_conviction_override": hypothesis.get(
            "high_conviction_override_active",
        ),
    }


def _classify_agreement(
    sovereign_action: str, council_action: str,
) -> str:
    s = (sovereign_action or "HOLD").upper()
    c = (council_action or "HOLD").upper()
    if s == c:
        return "AGREE"
    if s == "HOLD" or c == "HOLD":
        return "PARTIAL"  # one wants in, one wants to wait
    return "DISAGREE"


async def file_decision_pair(
    db: Any,
    *,
    sovereign_decision: Mapping[str, Any],
    council_hypothesis: Mapping[str, Any] | None,
    trace_id: Optional[str] = None,
    trade_id: Optional[str] = None,
) -> dict[str, Any]:
    """Insert a paired decision row into ``decision_pairs`` and
    return the persisted doc (without ``_id``).

    Idempotent on ``decision_id`` — if the same Sovereign decision is
    filed twice, the later call is a no-op. This protects against
    retry storms during sidecar reconnects.
    """
    sov_voice = render_voice(sovereign_decision)
    council_voice = _council_voice_from_hypothesis(council_hypothesis)

    symbol = sovereign_decision.get("symbol", "?")
    asset_type = sovereign_decision.get("asset_type", "?")
    decision_id = sovereign_decision.get("decision_id")
    if not decision_id:
        raise ValueError(
            "sovereign_decision must carry a decision_id for idempotent filing"
        )

    agreement = _classify_agreement(sov_voice["action"], council_voice["action"])

    doc = {
        "decision_id": decision_id,
        "symbol": symbol,
        "asset_type": asset_type,
        "lane": "crypto" if asset_type == "crypto" else "equity",
        "created_at": datetime.now(timezone.utc),
        "trace_id": trace_id,
        "trade_id": trade_id,
        "sovereign": sov_voice,
        "council": council_voice,
        "agreement": agreement,
        # Outcome placeholder — filled by ``decision_outcome_writer``
        # once the position closes.
        "resolved": False,
        "outcome": None,
    }

    # Idempotent insert (skip if a row for this decision already exists).
    existing = await db[COLLECTION].find_one(
        {"decision_id": decision_id}, {"_id": 0},
    )
    if existing:
        return existing

    await db[COLLECTION].insert_one(dict(doc))
    logger.info(
        "[%s] DECISION_PAIR_FILED symbol=%s sov=%s council=%s agreement=%s",
        trace_id or "--------", symbol, sov_voice["verdict"],
        council_voice["verdict"], agreement,
    )
    # Return without the auto-injected _id field.
    doc.pop("_id", None)
    return doc


async def fetch_recent_pairs(
    db: Any, *, limit: int = 50, lane: Optional[str] = None,
    agreement: Optional[str] = None, resolved: Optional[bool] = None,
) -> list[dict[str, Any]]:
    """List recent decision pairs for the dashboard.

    Filters are optional; all default to "no filter." The query
    always projects out ``_id``."""
    q: dict[str, Any] = {}
    if lane:
        q["lane"] = lane
    if agreement:
        q["agreement"] = agreement.upper()
    if resolved is not None:
        q["resolved"] = bool(resolved)

    cursor = db[COLLECTION].find(q, {"_id": 0}).sort("created_at", -1).limit(
        max(1, min(limit, 500)),
    )
    rows: list[dict[str, Any]] = []
    async for r in cursor:
        rows.append(r)
    return rows


__all__ = ["file_decision_pair", "fetch_recent_pairs", "COLLECTION"]
