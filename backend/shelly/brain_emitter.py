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


__all__ = ["emit_brain_hypothesis"]
