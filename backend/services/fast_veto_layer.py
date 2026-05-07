"""Tier 1 — Fast Veto Shadow Layer.

A sub-millisecond, deterministic, classical-ML veto layer that sits
between the hard kill-switch and the Council/Commander LLM consensus
path. **Veto-only authority**, never approves a trade. Stays in
shadow mode until the operator promotes it via env flag flip.

Pipeline position::

    candidate signal
       ↓
    hard kill checks (existing — ai_core.kill_switch)
       ↓
    FAST_VETO shadow check  ← THIS MODULE
       ↓
    Council / Commander / Risk Modulator (existing)

Authority guardrails (non-negotiable):

* ``FAST_VETO_CAN_APPROVE`` is hard-coded ``False``. The result
  envelope cannot ever instruct the executor to take a position.
* ``FAST_VETO_ENFORCE_ENABLED`` defaults ``False`` — even when
  ``would_veto`` is True, the executor only respects it after an
  operator-gated env flag flip. Until then this layer is pure
  observation, identical to Shelly's shadow rollout.
* The result of the rule cascade is a STOP signal or a no-op. There
  is no path from this module to direction/size/HOLD promotion.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)


FAST_VETO_SHADOW_ENABLED = os.getenv("FAST_VETO_SHADOW_ENABLED", "false").lower() == "true"
FAST_VETO_ENFORCE_ENABLED = os.getenv("FAST_VETO_ENFORCE_ENABLED", "false").lower() == "true"

# Hard safety: even if enforce is turned on, default to veto-only.
FAST_VETO_CAN_APPROVE = False

_FEATURE_WIDTH = 5
_FEATURE_BUFFER = np.zeros((_FEATURE_WIDTH,), dtype=np.float32)


@dataclass
class FastVetoResult:
    would_veto: bool
    enforce_veto: bool
    reason: str
    latency_us: float
    features: Dict[str, float]
    model_scores: Dict[str, float]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def build_fast_veto_features(
    signal: Dict[str, Any],
    market_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """Keep this tiny and stable.

    Feature meanings:

    1. confidence
    2. spread_bps
    3. volatility_score
    4. drawdown_pct
    5. liquidity_score
    """
    market_state = market_state or {}

    confidence = _safe_float(signal.get("confidence"), 0.0)
    spread_bps = _safe_float(market_state.get("spread_bps"), 0.0)
    volatility_score = _safe_float(market_state.get("volatility_score"), 0.0)
    drawdown_pct = _safe_float(market_state.get("drawdown_pct"), 0.0)
    liquidity_score = _safe_float(market_state.get("liquidity_score"), 1.0)

    return {
        "confidence": confidence,
        "spread_bps": spread_bps,
        "volatility_score": volatility_score,
        "drawdown_pct": drawdown_pct,
        "liquidity_score": liquidity_score,
    }


def _write_features_in_place(features: Dict[str, float]) -> np.ndarray:
    """Avoid reshape/allocation churn.

    sklearn can consume ``view.reshape(1, -1)``, but we only do it
    once per call. The buffer is module-level to skip the
    ``np.zeros`` allocation on the hot path.
    """
    _FEATURE_BUFFER[0] = features["confidence"]
    _FEATURE_BUFFER[1] = features["spread_bps"]
    _FEATURE_BUFFER[2] = features["volatility_score"]
    _FEATURE_BUFFER[3] = features["drawdown_pct"]
    _FEATURE_BUFFER[4] = features["liquidity_score"]

    return _FEATURE_BUFFER.reshape(1, -1)


def _rule_veto(features: Dict[str, float]) -> tuple[bool, str]:
    """Deterministic fallback. STOP only. Never approves a trade."""
    if features["drawdown_pct"] >= 10.0:
        return True, "FAST_VETO_DRAWDOWN_BREACH"

    if features["spread_bps"] >= 75.0:
        return True, "FAST_VETO_WIDE_SPREAD"

    if features["volatility_score"] >= 0.90 and features["confidence"] < 0.70:
        return True, "FAST_VETO_VOL_SPIKE_LOW_CONFIDENCE"

    if features["liquidity_score"] <= 0.20:
        return True, "FAST_VETO_LOW_LIQUIDITY"

    return False, "FAST_VETO_PASS_SHADOW"


def evaluate_fast_veto(
    signal: Dict[str, Any],
    market_state: Optional[Dict[str, Any]] = None,
    models: Optional[Dict[str, Any]] = None,
) -> FastVetoResult:
    """Main entry point.

    In shadow:
      - ``would_veto`` may be true
      - ``enforce_veto`` is always false

    In enforce:
      - ``enforce_veto`` can only become true when
        ``FAST_VETO_ENFORCE_ENABLED=true``
      - still veto-only — guarded by ``FAST_VETO_CAN_APPROVE = False``
    """
    started = time.perf_counter_ns()

    features = build_fast_veto_features(signal, market_state)
    x = _write_features_in_place(features)

    model_scores: Dict[str, float] = {}

    # Optional model path. Safe if models are not loaded yet.
    if models:
        for name, model in models.items():
            try:
                if hasattr(model, "predict_proba"):
                    score = float(model.predict_proba(x)[0][-1])
                else:
                    score = float(model.predict(x)[0])
                model_scores[name] = score
            except Exception:
                model_scores[name] = -1.0

    rule_veto, rule_reason = _rule_veto(features)

    model_veto = False
    model_reason = ""

    if model_scores:
        bad_scores = [score for score in model_scores.values() if score >= 0.80]
        if len(bad_scores) >= 2:
            model_veto = True
            model_reason = "FAST_VETO_MODEL_CONSENSUS"

    would_veto = rule_veto or model_veto
    reason = rule_reason if rule_veto else model_reason or "FAST_VETO_PASS_SHADOW"

    latency_us = (time.perf_counter_ns() - started) / 1_000.0

    enforce_veto = bool(
        FAST_VETO_ENFORCE_ENABLED
        and would_veto
        and not FAST_VETO_CAN_APPROVE
    )

    return FastVetoResult(
        would_veto=would_veto,
        enforce_veto=enforce_veto,
        reason=reason,
        latency_us=latency_us,
        features=features,
        model_scores=model_scores,
    )


async def log_fast_veto_delta(
    db: Any,
    *,
    signal: Dict[str, Any],
    market_state: Optional[Dict[str, Any]],
    result: FastVetoResult,
    council_result: Optional[Dict[str, Any]] = None,
) -> None:
    """Logs shadow deltas for Shelly / Health panel / later
    promotion proof. Never raises — the executor must not be
    affected by a logging failure.
    """
    if not FAST_VETO_SHADOW_ENABLED:
        return
    if db is None:
        return

    try:
        doc = {
            "created_at": datetime.now(timezone.utc),
            "symbol": signal.get("symbol"),
            "asset_type": signal.get("asset_type", "equity"),
            "action": signal.get("action") or signal.get("direction"),
            "confidence": signal.get("confidence"),
            "would_veto": result.would_veto,
            "enforce_veto": result.enforce_veto,
            "reason": result.reason,
            "latency_us": result.latency_us,
            "features": result.features,
            "model_scores": result.model_scores,
            "market_state": market_state or {},
            "council_action": (council_result or {}).get("action"),
            "council_confidence": (council_result or {}).get("confidence"),
            "agreement_with_council": _agreement_with_council(result, council_result),
            "schema_version": 1,
        }
        await db.fast_veto_shadow_deltas.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[fast-veto] shadow log insert failed: %s", exc)


def _agreement_with_council(
    result: FastVetoResult,
    council_result: Optional[Dict[str, Any]],
) -> Optional[bool]:
    if not council_result:
        return None

    council_action = str(council_result.get("action", "")).upper()

    if result.would_veto and council_action in {"HOLD", "NO_TRADE", "VETO", "SKIP", "SKIPPED"}:
        return True

    if result.would_veto and council_action in {"BUY", "SELL", "LONG", "SHORT"}:
        return False

    return None
