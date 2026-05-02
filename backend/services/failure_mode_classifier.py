"""
RISEDUAL Patent M — Failure Mode Intelligence

Purpose:
    Detect market/system failure regimes before execution and emit risk guidance.

This layer does not generate BUY/SELL/HOLD.
It classifies conditions such as volatility shock, liquidity trap, news shock,
calibration failure, or model instability, then recommends risk contraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FailureMode(str, Enum):
    NORMAL = "NORMAL"
    VOLATILITY_SHOCK = "VOLATILITY_SHOCK"
    LIQUIDITY_TRAP = "LIQUIDITY_TRAP"
    OPTIONS_LIQUIDITY_TRAP = "OPTIONS_LIQUIDITY_TRAP"
    LIQUIDITY_STRESS = "LIQUIDITY_STRESS"
    NEWS_SHOCK = "NEWS_SHOCK"
    CALIBRATION_FAILURE = "CALIBRATION_FAILURE"
    MODEL_INSTABILITY = "MODEL_INSTABILITY"
    DRAWDOWN_STRESS = "DRAWDOWN_STRESS"
    DATA_QUALITY_FAILURE = "DATA_QUALITY_FAILURE"


@dataclass(frozen=True)
class MarketTelemetry:
    symbol: str
    asset_type: str
    atr_pct: float
    atr_pct_baseline: float
    volume_zscore: float
    spread_bps: float
    spread_bps_baseline: float
    news_sentiment_abs: float = 0.0
    news_volume_zscore: float = 0.0
    data_missing_ratio: float = 0.0
    # Dollar volume telemetry — when the symbol's dollar-traded flow
    # collapses well below its baseline, the quoted spread may LOOK
    # tight only because no one is trading; a real attempt to size
    # into the name will cross a stale book and slip badly. Both
    # fields default to 0.0 so legacy callers that only pass the
    # spread-based fields don't trip the trap by accident — the
    # classifier requires BOTH a non-zero baseline AND a below-trigger
    # ratio before firing the dollar-volume branch.
    dollar_volume: float = 0.0
    dollar_volume_baseline: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelTelemetry:
    calibration_gap: float
    prediction_entropy: float
    confidence: float
    confidence_baseline: float
    disagreement_score: float
    recent_error_rate: float
    loss_streak: int
    max_drawdown: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FailureModeConfig:
    volatility_ratio_trigger: float = 2.0
    spread_ratio_trigger: float = 2.5
    spread_absolute_bps_trigger: float = 75.0
    volume_zscore_trigger: float = 3.0
    news_sentiment_abs_trigger: float = 0.80
    news_volume_zscore_trigger: float = 3.0
    data_missing_ratio_trigger: float = 0.10
    calibration_gap_trigger: float = 0.12
    entropy_trigger: float = 0.80
    disagreement_trigger: float = 0.75
    error_rate_trigger: float = 0.55
    loss_streak_trigger: int = 4
    drawdown_trigger: float = 0.10
    # Dollar-volume below `dollar_volume_ratio_trigger * baseline` trips
    # the LIQUIDITY_TRAP branch independently of spread. 0.30 = a 70%
    # collapse in traded dollars. Tuned conservative — a light-volume
    # day at 0.50x baseline is NOT a trap; only a severe drop.
    dollar_volume_ratio_trigger: float = 0.30


@dataclass(frozen=True)
class FailureModeResult:
    mode: FailureMode
    confidence: float
    risk_multiplier_cap: float
    block_trade: bool
    reasons: list[str]
    metadata: dict[str, Any]


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def classify_failure_mode(
    market: MarketTelemetry,
    model: ModelTelemetry,
    config: Optional[FailureModeConfig] = None,
) -> FailureModeResult:
    config = config or FailureModeConfig()

    candidates: list[FailureModeResult] = []

    atr_ratio = _safe_ratio(market.atr_pct, market.atr_pct_baseline)
    spread_ratio = _safe_ratio(market.spread_bps, market.spread_bps_baseline)

    if market.data_missing_ratio >= config.data_missing_ratio_trigger:
        candidates.append(
            FailureModeResult(
                mode=FailureMode.DATA_QUALITY_FAILURE,
                confidence=_clamp01(market.data_missing_ratio / 0.50),
                risk_multiplier_cap=0.0,
                block_trade=True,
                reasons=["data_missing_ratio_high"],
                metadata={"data_missing_ratio": market.data_missing_ratio},
            )
        )

    if atr_ratio >= config.volatility_ratio_trigger or market.volume_zscore >= config.volume_zscore_trigger:
        confidence = max(
            _clamp01(atr_ratio / 4.0),
            _clamp01(abs(market.volume_zscore) / 6.0),
        )
        candidates.append(
            FailureModeResult(
                mode=FailureMode.VOLATILITY_SHOCK,
                confidence=confidence,
                risk_multiplier_cap=0.50,
                block_trade=False,
                reasons=["atr_or_volume_volatility_shock"],
                metadata={"atr_ratio": atr_ratio, "volume_zscore": market.volume_zscore},
            )
        )

    if (
        spread_ratio >= config.spread_ratio_trigger
        or market.spread_bps >= config.spread_absolute_bps_trigger
    ):
        confidence = max(
            _clamp01(spread_ratio / 5.0),
            _clamp01(market.spread_bps / 150.0),
        )
        candidates.append(
            FailureModeResult(
                mode=FailureMode.LIQUIDITY_TRAP,
                confidence=confidence,
                risk_multiplier_cap=0.25,
                block_trade=True,
                reasons=["spread_widening_liquidity_trap"],
                metadata={"spread_ratio": spread_ratio, "spread_bps": market.spread_bps},
            )
        )

    # Dollar-volume starvation branch of LIQUIDITY_TRAP. Independent of
    # spread — a ghost book can quote tight while no real flow transacts,
    # and a market order at size will walk through stale resting liquidity.
    # Fires only when a baseline is available AND the live ratio clears
    # the trigger in the DEFICIT direction (ratio < trigger). Both sides
    # guard against the zero-baseline footgun — legacy callers that pass
    # the default 0.0 values can't trip this branch.
    dv_ratio = _safe_ratio(market.dollar_volume, market.dollar_volume_baseline)
    if (
        market.dollar_volume_baseline > 0
        and market.dollar_volume > 0
        and dv_ratio < config.dollar_volume_ratio_trigger
    ):
        # Higher deficit → higher confidence. dv_ratio=0.1 → confidence≈0.67,
        # dv_ratio=0.3 → confidence≈0.0 (right at the trigger edge).
        deficit = max(0.0, config.dollar_volume_ratio_trigger - dv_ratio)
        dv_confidence = _clamp01(deficit / config.dollar_volume_ratio_trigger)
        candidates.append(
            FailureModeResult(
                mode=FailureMode.LIQUIDITY_TRAP,
                confidence=dv_confidence,
                risk_multiplier_cap=0.25,
                block_trade=True,
                reasons=["dollar_volume_starvation"],
                metadata={
                    "dollar_volume_ratio": round(dv_ratio, 4),
                    "dollar_volume": market.dollar_volume,
                    "dollar_volume_baseline": market.dollar_volume_baseline,
                },
            )
        )

    if (
        market.news_sentiment_abs >= config.news_sentiment_abs_trigger
        and market.news_volume_zscore >= config.news_volume_zscore_trigger
    ):
        confidence = max(
            _clamp01(market.news_sentiment_abs),
            _clamp01(market.news_volume_zscore / 6.0),
        )
        candidates.append(
            FailureModeResult(
                mode=FailureMode.NEWS_SHOCK,
                confidence=confidence,
                risk_multiplier_cap=0.50,
                block_trade=False,
                reasons=["news_sentiment_and_volume_shock"],
                metadata={
                    "news_sentiment_abs": market.news_sentiment_abs,
                    "news_volume_zscore": market.news_volume_zscore,
                },
            )
        )

    if model.calibration_gap >= config.calibration_gap_trigger:
        candidates.append(
            FailureModeResult(
                mode=FailureMode.CALIBRATION_FAILURE,
                confidence=_clamp01(model.calibration_gap / 0.30),
                risk_multiplier_cap=0.50,
                block_trade=False,
                reasons=["calibration_gap_high"],
                metadata={"calibration_gap": model.calibration_gap},
            )
        )

    if (
        model.prediction_entropy >= config.entropy_trigger
        or model.disagreement_score >= config.disagreement_trigger
        or model.recent_error_rate >= config.error_rate_trigger
    ):
        confidence = max(
            _clamp01(model.prediction_entropy),
            _clamp01(model.disagreement_score),
            _clamp01(model.recent_error_rate),
        )
        candidates.append(
            FailureModeResult(
                mode=FailureMode.MODEL_INSTABILITY,
                confidence=confidence,
                risk_multiplier_cap=0.35,
                block_trade=False,
                reasons=["model_instability_detected"],
                metadata={
                    "prediction_entropy": model.prediction_entropy,
                    "disagreement_score": model.disagreement_score,
                    "recent_error_rate": model.recent_error_rate,
                },
            )
        )

    if model.loss_streak >= config.loss_streak_trigger or model.max_drawdown >= config.drawdown_trigger:
        confidence = max(
            _clamp01(model.loss_streak / 8.0),
            _clamp01(model.max_drawdown / 0.25),
        )
        candidates.append(
            FailureModeResult(
                mode=FailureMode.DRAWDOWN_STRESS,
                confidence=confidence,
                risk_multiplier_cap=0.25,
                block_trade=False,
                reasons=["drawdown_or_loss_streak_stress"],
                metadata={
                    "loss_streak": model.loss_streak,
                    "max_drawdown": model.max_drawdown,
                },
            )
        )

    if not candidates:
        return FailureModeResult(
            mode=FailureMode.NORMAL,
            confidence=1.0,
            risk_multiplier_cap=1.0,
            block_trade=False,
            reasons=["normal_conditions"],
            metadata={
                "atr_ratio": atr_ratio,
                "spread_ratio": spread_ratio,
            },
        )

    candidates.sort(
        key=lambda item: (
            item.block_trade,
            item.confidence,
            1.0 - item.risk_multiplier_cap,
        ),
        reverse=True,
    )

    return candidates[0]


def apply_failure_mode_to_multiplier(
    base_multiplier: float,
    failure: FailureModeResult,
) -> tuple[float, list[str]]:
    if failure.block_trade:
        return 0.0, [*failure.reasons, "failure_mode_blocked_trade"]

    final_multiplier = min(base_multiplier, failure.risk_multiplier_cap)

    reasons = list(failure.reasons)
    if final_multiplier < base_multiplier:
        reasons.append("failure_mode_tightened_risk")

    return final_multiplier, reasons


# ── OPTIONS_LIQUIDITY_TRAP (Phase 2b integration hook) ───────────────
#
# Pure function — options-awareness by explicit call only. The equity
# decision path continues to use ``classify_failure_mode`` unchanged; any
# caller that also wants to gate on options-market health now composes
# the two results with ``pick_tighter_failure``. Never-dominant rule:
# this mode caps the risk multiplier but does NOT ``block_trade`` — the
# underlying equity trade can still go through at reduced size.
#
# Trigger logic (per user spec):
#   * snapshot missing / symbol absent       → None (no-op)
#   * symbol present but zero hot contracts  → OPTIONS_LIQUIDITY_TRAP
#       (the liquid-flow filter rejected everything — market makers
#        likely pulled back, signal-from-options is unusable)
#   * symbol present, min spread_bps > 75    → OPTIONS_LIQUIDITY_TRAP
#       (even the tightest contract is wide — same conclusion)


OPTIONS_SPREAD_TRAP_BPS = 75.0

# LIQUIDITY_STRESS (stress-index) thresholds. Kept aligned with the bands
# defined in ``options_universe_service._aggregate_contracts`` so the two
# sides of the system agree on vocabulary. Stress = cap risk at 0.70×;
# instability = cap at 0.50×. Neither blocks the trade — equity path
# can still go through at reduced size ("additive, never dominant").
LIQUIDITY_STRESS_THRESHOLD = 4.0
LIQUIDITY_INSTABILITY_THRESHOLD = 6.0


def classify_options_liquidity(
    symbol: str,
    options_entry: Optional[dict],
) -> Optional[FailureModeResult]:
    """Per-symbol options-market liquidity check.

    ``options_entry`` is the per-symbol dict returned by
    ``options_universe_service.read_options_snapshot``. ``None`` signals
    "no snapshot / symbol not configured" and must round-trip to ``None``
    — that's the empty-snapshot guarantee every downstream hook relies on.

    Two distinct stressors are detected here. The tighter of the two wins
    (via the same pick_tighter_failure composition used for multi-mode).

      * ``OPTIONS_LIQUIDITY_TRAP`` — no hot-flow contracts, or the
        narrowest spread exceeds 75 bps. Surface-level un-tradeability.
      * ``LIQUIDITY_STRESS`` / instability — p90/avg ratio of the
        spread distribution crossed 4.0 (stress) or 6.0 (instability).
        This is the pre-volatility signal: the tail is widening
        asymmetrically vs the body, meaning MMs are pulling a subset
        of strikes even while the broader book looks OK.
    """
    if not options_entry:
        return None

    candidates: list[FailureModeResult] = []

    contracts = options_entry.get("contracts") or []
    if not contracts:
        candidates.append(FailureModeResult(
            mode=FailureMode.OPTIONS_LIQUIDITY_TRAP,
            confidence=0.60,
            risk_multiplier_cap=0.50,
            block_trade=False,
            reasons=["no_hot_flow_contracts"],
            metadata={"symbol": symbol.upper()},
        ))
    else:
        min_spread = min(
            float(c.get("spread_bps") or 9999) for c in contracts
        )
        if min_spread > OPTIONS_SPREAD_TRAP_BPS:
            candidates.append(FailureModeResult(
                mode=FailureMode.OPTIONS_LIQUIDITY_TRAP,
                confidence=_clamp01(min_spread / 150.0),
                risk_multiplier_cap=0.50,
                block_trade=False,
                reasons=["options_spread_widening"],
                metadata={
                    "symbol": symbol.upper(),
                    "min_spread_bps": round(min_spread, 2),
                },
            ))

    aggregate = options_entry.get("aggregate") or {}
    stress_idx = aggregate.get("liquidity_stress_index")
    if stress_idx is not None:
        try:
            idx = float(stress_idx)
        except (TypeError, ValueError):
            idx = None
        if idx is not None and idx >= LIQUIDITY_STRESS_THRESHOLD:
            # Tighter cap for instability (>6) than plain stress (>4).
            if idx >= LIQUIDITY_INSTABILITY_THRESHOLD:
                cap = 0.50
                reasons = ["liquidity_instability_imminent"]
                confidence = 0.85
            else:
                cap = 0.70
                reasons = ["liquidity_stress_building"]
                confidence = 0.65
            candidates.append(FailureModeResult(
                mode=FailureMode.LIQUIDITY_STRESS,
                confidence=confidence,
                risk_multiplier_cap=cap,
                block_trade=False,
                reasons=reasons,
                metadata={
                    "symbol": symbol.upper(),
                    "liquidity_stress_index": round(idx, 2),
                    "stress_level": aggregate.get("stress_level"),
                },
            ))

    if not candidates:
        return None
    # Tightest cap wins (mirror pick_tighter_failure's own logic without
    # importing it — this function is also used standalone).
    candidates.sort(key=lambda r: (not r.block_trade, r.risk_multiplier_cap))
    return candidates[0]


def pick_tighter_failure(
    *results: Optional[FailureModeResult],
) -> Optional[FailureModeResult]:
    """Compose multiple failure-mode verdicts into the most conservative
    one. ``None`` values are dropped. Used when a caller runs both
    ``classify_failure_mode`` (equity) and ``classify_options_liquidity``
    (options) and wants a single verdict to feed the risk modulator.

    Priority: block_trade dominates, then lowest risk_multiplier_cap wins.
    """
    filtered = [r for r in results if r is not None]
    if not filtered:
        return None
    # block_trade first, then tightest cap
    filtered.sort(
        key=lambda r: (not r.block_trade, r.risk_multiplier_cap),
    )
    return filtered[0]


def options_stress_size_multiplier(options_entry: Optional[dict]) -> float:
    """Pure opt-in size modulator driven by liquidity stress index.

    Caller pattern:
        size *= options_stress_size_multiplier(options_entry)

    Returns:
      * ``1.0`` when the snapshot is empty, stress is normal/cautious,
        or the stress index is unavailable. Keeps the "additive,
        never-dominant" rule — defaults to no effect.
      * ``0.70`` when stress ≥ 4 (stress regime)
      * ``0.50`` when stress ≥ 6 (instability imminent)

    This mirrors the risk_multiplier_cap in classify_options_liquidity
    so both code paths reach the same decisions independently — callers
    can use whichever composition surface fits their context.
    """
    if not options_entry:
        return 1.0
    aggregate = options_entry.get("aggregate") or {}
    idx = aggregate.get("liquidity_stress_index")
    if idx is None:
        return 1.0
    try:
        value = float(idx)
    except (TypeError, ValueError):
        return 1.0
    if value >= LIQUIDITY_INSTABILITY_THRESHOLD:
        return 0.50
    if value >= LIQUIDITY_STRESS_THRESHOLD:
        return 0.70
    return 1.0
