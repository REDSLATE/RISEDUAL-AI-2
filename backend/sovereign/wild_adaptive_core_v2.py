"""Sovereign doctrine + deterministic adaptive core — Alpha brain.

Two responsibilities live here:

1. **Doctrine constants** — the three-lock door:
       Lock 1 — `LIVE_TRADING_ENABLED` is literally False at module level.
       Lock 2 — the sidecar's `_assert_doctrine()` raises if anyone flips it.
       Lock 3 — MC's API rejects `live_trading_enabled: true` with 422.

2. **The deterministic adaptive core** — `run_adaptive_core(...)` consumes a
   top-of-book snapshot + the brain's current weights and returns a
   `Decision` dataclass. No I/O, no randomness, fully unit-testable.
   `update_weights(...)` applies a one-step gradient given a resolved outcome.

If you find yourself wanting to set ``LIVE_TRADING_ENABLED = True`` for
"testing": stop. Synthetic feeds give you everything you need in Phase 1.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

# ── LOCK #1 ────────────────────────────────────────────────────────────
LIVE_TRADING_ENABLED: bool = False

# Cosmetic identity — used in logs + MC's runtime page header.
BRAIN_NAME: str = "alpha"
BRAIN_PERSONALITY: str = "trend_follower"
SUPPORTED_MODES: tuple[str, ...] = ("DTD", "PRD")

# Vocabularies (validated everywhere these appear).
ALLOWED_ACTIONS: frozenset[str] = frozenset({"BUY", "SELL", "HOLD"})
ALLOWED_OUTCOMES: frozenset[int] = frozenset({-1, 0, 1})
ACTION_TO_STANCE: dict[str, str] = {
    "BUY": "long",
    "SELL": "short",
    "HOLD": "abstain",
}

# Adaptive-core tuning.
_BUY_THRESHOLD = 0.05
_SELL_THRESHOLD = -0.05
_WEIGHT_MIN, _WEIGHT_MAX = -3.0, 3.0
_CONFIDENCE_GAIN = 2.0  # confidence = min(1, |score| * gain)

__all__ = [
    "LIVE_TRADING_ENABLED",
    "BRAIN_NAME",
    "BRAIN_PERSONALITY",
    "SUPPORTED_MODES",
    "ALLOWED_ACTIONS",
    "ALLOWED_OUTCOMES",
    "ACTION_TO_STANCE",
    "Decision",
    "asdict",
    "assert_doctrine",
    "assert_safe_action",
    "default_weights",
    "extract_features",
    "map_action_to_stance",
    "run_adaptive_core",
    "update_weights",
]


def assert_doctrine() -> None:
    """LOCK #2. Sidecar calls this on boot and refuses to start on failure."""
    if LIVE_TRADING_ENABLED is not False:
        raise RuntimeError(
            "DOCTRINE VIOLATION: wild_adaptive_core_v2.LIVE_TRADING_ENABLED "
            "is not False. Sidecar refusing to start. Reset to False and "
            "redeploy."
        )


def assert_safe_action(action: str) -> None:
    """Hard guard: action must be in the allowed set AND live trading must
    still be off. The sidecar calls this after every decision."""
    if action not in ALLOWED_ACTIONS:
        raise ValueError(
            f"unknown action {action!r}; must be one of {sorted(ALLOWED_ACTIONS)}"
        )
    if LIVE_TRADING_ENABLED:
        raise RuntimeError(
            "DOCTRINE VIOLATION: assert_safe_action called while "
            "LIVE_TRADING_ENABLED is True."
        )


def map_action_to_stance(action: str) -> str:
    """Translate the core's action vocabulary to MC's stance vocabulary."""
    if action not in ACTION_TO_STANCE:
        raise ValueError(
            f"cannot map action {action!r} to stance "
            f"(known: {sorted(ACTION_TO_STANCE)})"
        )
    return ACTION_TO_STANCE[action]


def default_weights() -> dict[str, float]:
    """Alpha's identity: trend-follower bias.

    Negative RSI weight = "don't get tempted by oversold". This is what
    differentiates Alpha from Camaro/Chevelle/Redeye.
    """
    return {"trend": 0.85, "macd": 0.65, "rsi": -0.25}


@dataclass
class Decision:
    """Output of `run_adaptive_core`. Pure data, JSON-friendly via asdict()."""

    symbol: str
    action: str
    confidence: float
    score: float
    features: dict[str, float] = field(default_factory=dict)
    confidence_origin: dict[str, float] = field(default_factory=dict)


def extract_features(top: Mapping[str, Any]) -> dict[str, float]:
    """Pull the three canonical features out of a top-of-book snapshot."""
    if not isinstance(top, Mapping):
        raise ValueError(f"top-of-book must be a mapping, got {type(top).__name__}")
    tech = top.get("technicals") or {}
    if not isinstance(tech, Mapping):
        tech = {}

    try:
        price = float(top.get("price", 0.0) or 0.0)
    except (TypeError, ValueError):
        price = 0.0
    try:
        sma20 = float(tech.get("sma20", 0.0) or 0.0)
    except (TypeError, ValueError):
        sma20 = 0.0
    try:
        macd_val = float(tech.get("macd", 0.0) or 0.0)
    except (TypeError, ValueError):
        macd_val = 0.0
    try:
        rsi14 = float(tech.get("rsi14", 50.0) or 50.0)
    except (TypeError, ValueError):
        rsi14 = 50.0

    trend = (price - sma20) / sma20 if sma20 else 0.0
    rsi = (rsi14 - 50.0) / 50.0

    feats = {"trend": trend, "macd": macd_val, "rsi": rsi}
    return {k: (v if math.isfinite(v) else 0.0) for k, v in feats.items()}


def run_adaptive_core(
    top: Mapping[str, Any],
    weights: Mapping[str, float],
    account_size: float = 0.0,  # noqa: ARG001 — kept for forward compat
) -> Decision:
    """Deterministic decision function. Same (top, weights) → same Decision."""
    symbol = str(top.get("symbol", "")) if isinstance(top, Mapping) else ""
    feats = extract_features(top)

    score = 0.0
    contribution: dict[str, float] = {}
    for k, f in feats.items():
        w = float(weights.get(k, 0.0) or 0.0) if isinstance(weights, Mapping) else 0.0
        c = w * f
        contribution[k] = c
        score += c

    if score > _BUY_THRESHOLD:
        action = "BUY"
    elif score < _SELL_THRESHOLD:
        action = "SELL"
    else:
        action = "HOLD"

    confidence = min(1.0, abs(score) * _CONFIDENCE_GAIN)
    if not math.isfinite(confidence) or confidence < 0.0:
        confidence = 0.0
    if action == "HOLD":
        confidence = min(confidence, 0.25)

    return Decision(
        symbol=symbol,
        action=action,
        confidence=confidence,
        score=score,
        features=feats,
        confidence_origin=contribution,
    )


def update_weights(
    weights: Mapping[str, float],
    features: Mapping[str, float],
    outcome: int,
    *,
    lr: float = 0.06,
) -> dict[str, float]:
    """One-step gradient update.

    ``outcome`` is the resolved trade label: +1 (correct), 0 (neutral),
    -1 (wrong). For each feature, push the weight toward the right call:
    ``w += lr * outcome * feature_value``. Clamped to ``[-3.0, +3.0]``.

    Pure function — does NOT mutate ``weights`` in place.
    """
    if outcome not in ALLOWED_OUTCOMES:
        raise ValueError(f"outcome must be -1/0/+1, got {outcome!r}")
    lr_f = float(lr)
    if not math.isfinite(lr_f) or lr_f < 0.0 or lr_f > 0.5:
        raise ValueError(f"learning rate {lr!r} outside [0.0, 0.5]")

    out: dict[str, float] = dict(weights)
    for k, fv in features.items():
        try:
            f_val = float(fv)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(f_val):
            continue
        nw = float(out.get(k, 0.0)) + lr_f * int(outcome) * f_val
        nw = max(_WEIGHT_MIN, min(_WEIGHT_MAX, nw))
        out[k] = nw
    return out
