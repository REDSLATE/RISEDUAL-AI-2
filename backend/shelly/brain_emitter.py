"""Phase 2 brain wiring — Shelly emission from
``multi_model_hypothesis_service._run_single_model``.

Extracted as a standalone helper so the main service file stays
under its preferred-size ceiling. Same behaviour as the inlined
block: maps ``model_key`` → federation node, normalises verdict +
confidence, and routes through the singleton Shelly pipeline.

Fail-soft: any error swallowed at DEBUG. Never affects the brain
receipt returned to the consensus aggregator.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


_MODEL_KEY_TO_NODE = {
    "alpha": "Alpha",
    "camaro": "Camaro",
    "chevelle": "Chevelle",
    "redeye": "RedEye",
}


def _normalise_direction(verdict: str) -> str:
    """Map a verdict token to LONG/SHORT/HOLD via the canonical
    boundary normaliser. HOLD here means "not a directional trade"
    (shelly memory stores HOLD on UNKNOWN/HOLD verdicts so the
    decision is still remembered)."""
    from services.prediction_tracker import canonical_ai_dir
    canon = canonical_ai_dir(verdict)
    # canonical_ai_dir returns LONG / SHORT / UNKNOWN; we map the
    # third bucket to HOLD so the receipt is still recorded.
    if canon == "UNKNOWN":
        return "HOLD"
    return canon


def _normalise_confidence(raw: Any) -> float:
    try:
        c = float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    # Hypothesis confidences are 0–100; Shelly stores 0–1.
    return c / 100.0 if c > 1.0 else c


async def emit_brain_hypothesis(
    *,
    model_key: str,
    symbol: str,
    hypothesis: dict[str, Any],
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """Fire-and-forget Shelly emission for one brain hypothesis."""
    try:
        from shelly.mc_emitter import get_pipeline
        pipeline = get_pipeline()
        if pipeline is None:
            return
        node = _MODEL_KEY_TO_NODE.get((model_key or "").lower())
        if node is None:
            return
        verdict = (hypothesis.get("verdict") or "").upper()
        await pipeline.record_brain_event(
            node,
            {
                "symbol": (symbol or "").upper(),
                "direction": _normalise_direction(verdict),
                "confidence": _normalise_confidence(
                    hypothesis.get("confidence"),
                ),
                "decision": verdict or "UNKNOWN",
                "features": {
                    "model": model,
                    "provider": provider,
                    "served_from_cache": False,
                    "verdict_raw": verdict,
                },
                "mc_status": "unverified",
                "roadguard_status": "n/a",
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[shelly_brain_emit] non-fatal model=%s sym=%s: %s",
            model_key, symbol, exc,
        )


async def emit_alpha_paper_trade(
    *,
    symbol: str,
    direction: str,
    confidence: Any,
    trade_id: str,
    prediction_id: Optional[str] = None,
    sovereign_decision_id: Optional[str] = None,
    entry_price: Optional[float] = None,
    position_usd: Optional[float] = None,
    regime: Optional[str] = None,
    mc_status: str = "unverified",
    roadguard_status: str = "n/a",
) -> None:
    """Fire-and-forget Shelly emission for an Alpha paper-trade.

    This is the *execution-stage* receipt — distinct from the
    hypothesis-stage receipt ``emit_brain_hypothesis`` writes.
    The hypothesis layer captures Alpha's *opinion* at signal
    generation; this captures Alpha's *committed action* after
    every gate has cleared (Kelly, RoadGuard, operator gate,
    memory modulator). Both layers will eventually receive the
    same outcome backfill — so the operator can compare
    "hypothesis-stage vs execution-stage" hit rates per brain.
    """
    try:
        from shelly.mc_emitter import get_pipeline
        pipeline = get_pipeline()
        if pipeline is None:
            return
        await pipeline.record_brain_event(
            "Alpha",
            {
                "symbol": (symbol or "").upper(),
                "direction": _normalise_direction(direction),
                "confidence": _normalise_confidence(confidence),
                "decision": "PAPER_TRADE_OPEN",
                "features": {
                    "trade_id": trade_id,
                    "prediction_id": prediction_id,
                    "sovereign_decision_id": sovereign_decision_id,
                    "entry_price": entry_price,
                    "position_usd": position_usd,
                    "regime": regime,
                    "stage": "execution",
                },
                "mc_status": mc_status,
                "roadguard_status": roadguard_status,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[shelly_brain_emit] alpha paper trade emit non-fatal sym=%s: %s",
            symbol, exc,
        )


__all__ = ["emit_brain_hypothesis", "emit_alpha_paper_trade"]
