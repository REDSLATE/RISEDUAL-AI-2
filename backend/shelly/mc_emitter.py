"""MC-side emitter for Shelly federation (Phase 1).

Provides a single helper, ``emit_mc_event``, that any MC verifier /
notary code path can call to push its verdict into the Shelly-MC
LocalShelly. The helper shapes MC's verdict into the standard
``ShellyMemoryEvent`` schema and routes it through the same
``ShellyPipeline.record_brain_event`` entry point the brain
emission sites will use in Phase 2.

This module is the **ONLY** sanctioned write path for MC → Shelly.
Other MC code calling Shelly collections directly would bypass the
memory-reasoning-only doctrine stamping; this helper guarantees the
stamp is applied.

Design constraints
------------------
* **Fail-soft.** Any error in emission is swallowed and logged at
  DEBUG. MC verification logic never blocks on Shelly. This is the
  same discipline used for all the other observation-only sidecars
  (Sovereign sidecar, RoadGuard shadow, etc.).
* **Lazy pipeline lookup.** The shared singleton lives on
  ``app.state.shelly_pipeline``. If it's missing (test, early boot,
  background task), we simply skip — Shelly is not load-bearing.
* **No execution authority leak.** Receipt dict is built such that
  ``decision`` reflects what MC SAID (e.g. ``"PROMOTE_GATE_PASS"``)
  not what should happen. The pipeline stamps
  ``authority: memory_reasoning_only`` on every doc.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Singleton pipeline accessor ──────────────────────────────────


_pipeline: Any = None


def set_pipeline(pipeline: Any) -> None:
    """Wired by ``server.py`` startup. Idempotent."""
    global _pipeline
    _pipeline = pipeline


def get_pipeline() -> Any:
    return _pipeline


# ── Public emit API ──────────────────────────────────────────────


async def emit_mc_event(
    *,
    verdict_type: str,
    symbol: str,
    direction: str,
    confidence: float = 1.0,
    decision: str = "MC_VERDICT",
    features: Optional[dict[str, Any]] = None,
    mc_status: str = "self",
    roadguard_status: str = "n/a",
    outcome: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Emit one MC verifier/notary receipt into Shelly-MC.

    Returns a small status dict ``{"ok": bool, "skipped"?: reason}``
    so callers that care about visibility can log; most callers will
    ignore the return value entirely.

    Parameters
    ----------
    verdict_type
        Short label for the verifier surface that produced this
        receipt — e.g. ``"sovereign_promotion_gate"``,
        ``"council_policy"``, ``"stage_3_5_matrix"``. Stored on the
        receipt's ``features`` so the operator can filter by source.
    symbol, direction
        Whatever the verdict applies to. For asset-level verdicts
        (promotion gate per equity/crypto) use the asset_type as
        the "symbol" and a meaningful direction tag (e.g.
        ``"PROMOTE"`` / ``"HOLD_SHADOW"``).
    confidence
        MC's own confidence in its verdict — 1.0 by default since
        the gate is deterministic.
    decision
        What MC DECIDED. Examples: ``"PROMOTE_GATE_PASS"``,
        ``"PROMOTE_GATE_BLOCK"``, ``"POLICY_FLOOR_RAISED"``.
    features
        Optional structured detail (rows_resolved, win_rate, etc.).
    """
    pipeline = get_pipeline()
    if pipeline is None:
        return {"ok": False, "skipped": "pipeline_uninitialised"}

    feats = dict(features or {})
    feats["verdict_type"] = verdict_type

    try:
        result = await pipeline.record_brain_event(
            "MC",
            {
                "symbol": symbol,
                "direction": direction,
                "confidence": float(confidence),
                "decision": decision,
                "features": feats,
                "mc_status": mc_status,
                "roadguard_status": roadguard_status,
                "outcome": outcome,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[shelly_mc_emitter] emit failed verdict=%s symbol=%s: %s",
            verdict_type, symbol, exc,
        )
        return {"ok": False, "skipped": "emit_exception", "error": str(exc)}

    return {"ok": bool(result.get("ok")), "result": result}


__all__ = ["emit_mc_event", "set_pipeline", "get_pipeline"]
