"""
Smart Money Verification — Patent J proof-chain block.

Writes a ``SMART_MONEY_VERIFIED`` block to the ``decision_proof_chain``
for a given decision entity. The block records whether the aggregate
13F smart-money flow agrees, disagrees, or is silent on the strategist's
directional intent at decision time.

Design contract
---------------
1. **Pure verification, never a veto**. The block is an audit record —
   downstream gates (risk budget, failure mode) may choose to read it,
   but this service never blocks a trade on its own. Same discipline as
   the options-flow narrative enrichment in ``adversarial_core``:
   additive, never dominant.
2. **Fail-safe on missing data**. If the 13F universe hasn't been
   refreshed (e.g., small-cap symbol), OR smart_money_score returns
   ``signal="no_data"``, we STILL write a proof block — with
   ``alignment="no_data"`` — so the chain has an explicit record that
   verification was *attempted*. A silent skip makes the chain
   ambiguous after the fact: was verification run? Was there a bug?
3. **Never raises into caller**. All exceptions are logged + swallowed;
   caller (IP contract, manual order guard) receives ``None`` and
   continues. An observability blip must not block a trade.

Alignment rule
--------------
* ``strategist_action=LONG`` + ``smart_money_signal=bullish`` → ``confirms``
* ``strategist_action=LONG`` + ``smart_money_signal=bearish`` → ``contradicts``
* ``strategist_action=SHORT`` + ``smart_money_signal=bearish`` → ``confirms``
* ``strategist_action=SHORT`` + ``smart_money_signal=bullish`` → ``contradicts``
* ``smart_money_signal=neutral`` → ``neutral``
* ``smart_money_signal=no_data`` → ``no_data``

Action strings tolerate synonyms (``BUY``/``STRONG_BUY`` → LONG etc.) —
same contract as ``commander_phase2_brake``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from services.proof_chain import (
    AsyncMongoProofChainStore,
    ProofEvent,
    ProofEventType,
    async_append_proof_event,
)

logger = logging.getLogger(__name__)


# Same action-synonym folding as commander_phase2_brake — kept local to
# keep this module independent.
_LONG_SYNONYMS = {"LONG", "BUY", "STRONG_BUY"}
_SHORT_SYNONYMS = {"SHORT", "SELL", "STRONG_SELL", "SHORT_OR_AVOID"}


def _canonical_action(action: Optional[str]) -> Optional[str]:
    if not action:
        return None
    key = str(action).strip().upper()
    if key in _LONG_SYNONYMS:
        return "LONG"
    if key in _SHORT_SYNONYMS:
        return "SHORT"
    return None


def compute_alignment(
    strategist_action: Optional[str],
    smart_money_signal: Optional[str],
) -> str:
    """Pure function: map (action, signal) → one of
    ``confirms | contradicts | neutral | no_data``.

    Unknown spellings degrade to ``no_data`` — consistent with the
    fail-safe discipline of the proof-chain block. Tested end-to-end.
    """
    action = _canonical_action(strategist_action)
    sig = (smart_money_signal or "").strip().lower()

    if sig == "no_data" or sig == "":
        return "no_data"
    if sig == "neutral":
        return "neutral"
    if action is None:
        return "no_data"

    if action == "LONG" and sig == "bullish":
        return "confirms"
    if action == "LONG" and sig == "bearish":
        return "contradicts"
    if action == "SHORT" and sig == "bearish":
        return "confirms"
    if action == "SHORT" and sig == "bullish":
        return "contradicts"

    return "no_data"


def build_verification_payload(
    *,
    symbol: str,
    strategist_action: Optional[str],
    smart_money_score: dict[str, Any],
) -> dict[str, Any]:
    """Shape the payload that lands inside the proof block.

    Kept small and schema-stable so the chain-verifier's
    ``payload_hash`` stays deterministic. The original
    ``smart_money_score`` dict can include large contributor lists —
    we store only the top 3 and the aggregated metrics. Full detail
    is always available from ``compute_smart_money_score`` on demand.
    """
    score = smart_money_score or {}
    sig = score.get("signal") or "no_data"
    alignment = compute_alignment(strategist_action, sig)

    contributors = score.get("contributors") or []
    top_contributors = [
        {
            "institution": c.get("institution_name") or c.get("institution"),
            "type": c.get("type"),
            "delta_value_usd": c.get("delta_value_usd"),
        }
        for c in contributors[:3]
    ]

    return {
        "symbol": (symbol or "").upper(),
        "strategist_action": _canonical_action(strategist_action) or str(strategist_action or ""),
        "alignment": alignment,
        "smart_money_signal": sig,
        "smart_money_score": score.get("score"),
        "holder_count": score.get("holder_count"),
        "bullish_count": score.get("bullish_count"),
        "bearish_count": score.get("bearish_count"),
        "neutral_count": score.get("neutral_count"),
        "net_flow_usd": score.get("net_flow_usd"),
        "total_value_usd": score.get("total_value_usd"),
        "top_contributors": top_contributors,
    }


async def verify_and_append(
    db: Any,
    *,
    entity_id: str,
    symbol: str,
    strategist_action: Optional[str],
    actor: str = "smart_money_verifier",
) -> Optional[dict[str, Any]]:
    """Fetch smart-money score, build payload, append proof block.

    Returns the payload dict on success (caller may use it for inline
    telemetry), ``None`` on any error. The proof block is the primary
    artefact — the returned dict is a convenience.

    Usage pattern (from inside an IP contract or manual order guard)::

        from services.smart_money_verification import verify_and_append
        payload = await verify_and_append(
            db,
            entity_id=proof_chain_entity_id,
            symbol=signal.symbol,
            strategist_action=signal.direction,
        )
        # Optionally fold `payload["alignment"]` into downstream telemetry.
    """
    if db is None:
        return None

    try:
        from services.sec_13f_service import compute_smart_money_score
        score = await compute_smart_money_score(db, symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[smart-money-verify] score fetch failed for %s: %s", symbol, exc,
        )
        # Still write a proof block with ``alignment=no_data`` so the
        # chain contains the verification attempt.
        score = {
            "symbol": (symbol or "").upper(),
            "score": None,
            "signal": "no_data",
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "net_flow_usd": 0,
            "total_value_usd": 0,
            "contributors": [],
            "fetch_error": str(exc)[:200],
        }

    payload = build_verification_payload(
        symbol=symbol,
        strategist_action=strategist_action,
        smart_money_score=score,
    )

    try:
        store = AsyncMongoProofChainStore(db)
        event = ProofEvent(
            event_type=ProofEventType.SMART_MONEY_VERIFIED,
            entity_id=entity_id,
            payload=payload,
            actor=actor,
        )
        await async_append_proof_event(store, event)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[smart-money-verify] proof append failed for entity=%s symbol=%s: %s",
            entity_id, symbol, exc,
        )
        return None

    return payload
