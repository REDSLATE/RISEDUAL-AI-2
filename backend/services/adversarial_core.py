"""Adversarial Decision Core — Bull / Bear / Commander (SHADOW only).

Why this exists
---------------
The existing crypto pipeline (Strategist + Auditor) is a two-stage
verification with a built-in agreement bias — the Auditor's veto rate
caps loss but both agents share the same objective and indicator set.
That's a *consensus* system, not an *adversarial* one.

This module introduces a **true adversarial** layer:

* :func:`bull_agent` profits if price goes UP. Its expected_R is high
  when momentum + trend confirm; it ignores reversal risk by design.
* :func:`bear_agent` profits if price goes DOWN. Its expected_R is high
  when RSI is overbought + volatility is expanding; it ignores
  trend-continuation by design.
* :func:`resolve_adversarial` resolves the conflict by computing
  ``score = confidence × expected_r`` for each side and using the gap.

Key property: **one of Bull/Bear is always wrong**, by construction.
That gives the system unambiguous ground truth to learn from after
each closed trade — unlike a consensus system where "we held and were
right not to fire" is impossible to score.

Architectural rule (SHADOW + double gate)
------------------------------------------
This entire module is gated behind TWO conditions, both of which must
be True before any agent runs or any decision is logged:

1. **ML Tier 3 unlocked** — read from
   :func:`services.tier3_readiness.check_tier3_unlock`. Prevents the
   adversarial layer from running until the underlying prediction
   labeler has produced enough evidence to trust the system at all.

2. **Env flag** ``CRYPTO_ADVERSARIAL_ENABLED=1`` — final ops switch.
   Lets you flip the layer off instantly even after Tier 3 unlocks.

When either gate is closed, :func:`run_adversarial_decision` returns
``None`` immediately, doing zero work.

Phase progression (same shape as web-research shadow lane)
-----------------------------------------------------------
Even when both gates are open, the layer starts in ``shadow`` phase —
log-only, never alters the live trade. Phase is read from
``CRYPTO_ADVERSARIAL_PHASE`` env var. Valid values:

* ``shadow`` (default) — log decisions, return None to caller.
  Used to accumulate 50–100 logged adversarial decisions before
  promoting to influence.
* ``risk_only`` — log + return ``risk_multiplier`` (0–1). Caller
  may scale position size by this. Direction is NEVER overridden.
* ``veto`` — log + may return ``decision="NO_TRADE"`` to block fills.
* ``full`` — log + may return ``decision="LONG"`` / ``"SHORT"``
  overriding the strategist/auditor entirely. Earned, not granted.

The gates and phases are independent: even in ``full`` phase, if Tier
3 is locked OR the env flag is off, this module is silent.

Calibration note (vs. user-supplied scaffold)
---------------------------------------------
The original scaffold used:

    confidence = 0.5 + (momentum * 0.2) + (trend * 0.2)

But ``momentum_5b`` in this codebase is on the scale 0–0.05 (i.e. a
5-bar % change), not 0–1. Plugging it in directly would make the
weight contribution ~0.01 — effectively zero against the 0.5 baseline.
This module normalises every input to a 0–1 range BEFORE scoring, so
the heuristic actually moves with the data.

Real per-regime weight tuning happens AFTER 100+ logged decisions
land in ``crypto_adversarial_decision_log``. See ROADMAP P2.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Tunables ──────────────────────────────────────────────────────────────────

# Both gates must be ENABLED for any agent to run. Env flag check first
# (cheap), Tier 3 check second (DB hit). Default-off behaviour: if the
# env var is missing, we treat it as disabled.
ENV_ENABLE_FLAG = "CRYPTO_ADVERSARIAL_ENABLED"
ENV_PHASE_FLAG = "CRYPTO_ADVERSARIAL_PHASE"

VALID_PHASES = ("shadow", "risk_only", "veto", "full")

# Decision threshold on edge_gap = bull_score - bear_score.
# If |gap| < EDGE_GAP_THRESHOLD → Commander says NO_TRADE.
# Tuned conservative; refine empirically once 100+ decisions logged.
EDGE_GAP_THRESHOLD = 0.35

# Risk multiplier ceiling — never let a high-confidence Bull win
# inflate position size beyond what the strategist already authorised.
# Multiplier ∈ [0, 1] always.
RISK_MULTIPLIER_CAP = 1.0


# ── Agent output container ────────────────────────────────────────────────────


@dataclass
class AgentOutput:
    """Pure dataclass — JSON-serialisable for Mongo. NO Mongo deps."""
    side: str
    confidence: float
    expected_r: float
    thesis: str
    invalidations: list[str]


# ── Input normalisation ───────────────────────────────────────────────────────


def _extract_inputs(signal: dict[str, Any]) -> dict[str, float]:
    """Pull indicators out of the strategist's signal dict and
    normalise to 0–1 ranges.

    The strategist returns:
        signal["strategist"]["indicators"] = {
            "rsi": 63.0,           # 0–100
            "ema20": 69500.0,      # absolute price
            "momentum_5b": 0.024,  # signed % over 5 bars
        }
        signal["regime"]   = "trending" | "parabolic" | ...

    Returns floats in the [0, 1] range so the heuristic weights
    actually move with the data.
    """
    strategist = signal.get("strategist") or {}
    indicators = strategist.get("indicators") or {}
    rsi = float(indicators.get("rsi") or 50.0)
    momentum_raw = float(indicators.get("momentum_5b") or 0.0)
    regime = (signal.get("regime") or "").lower()
    auditor = signal.get("auditor") or {}

    # rsi → 0–1
    rsi_norm = max(0.0, min(rsi / 100.0, 1.0))

    # momentum: tanh-squash a 5-bar % move into 0–1, with the
    # 0 point at no-momentum. Use scale=20 so a +5% bar lands ~0.76,
    # +2.5% lands ~0.46, -2.5% lands ~-0.46 → shifted to [0, 1].
    momentum_signed = math.tanh(momentum_raw * 20.0)   # [-1, 1]
    momentum_unit = (momentum_signed + 1.0) / 2.0      # [0, 1]; 0.5 = neutral

    # Trend proxy: how far is price above EMA20, as a fraction of EMA20?
    # Strategist already encodes this in its confidence — reuse rather
    # than recompute from raw price (which we don't have here).
    trend_proxy = float(strategist.get("confidence") or 0.5)

    # Volatility proxy: parabolic regime = high vol; trending = mid;
    # range/uncertain = low. Used by Bear which profits from
    # mean-reversion in volatile markets.
    vol_by_regime = {
        "parabolic": 0.85,
        "overbought": 0.75,
        "oversold": 0.75,
        "trending": 0.50,
        "trend_up": 0.50,
        "trend_down": 0.50,
        "range": 0.30,
        "uncertain": 0.40,
    }
    volatility = vol_by_regime.get(regime, 0.40)

    return {
        "rsi": rsi_norm,
        "momentum_signed": momentum_signed,    # in [-1, 1] for direction
        "momentum_unit": momentum_unit,        # in [0, 1] for magnitude
        "trend": trend_proxy,
        "volatility": volatility,
        "auditor_conf": float(auditor.get("confidence") or 0.5),
        "regime": regime,
    }


# ── Bull agent ────────────────────────────────────────────────────────────────


def bull_agent(signal: dict[str, Any]) -> AgentOutput:
    """Profits if price goes UP. Ignores reversal risk by design.

    Confidence rises with:
      * positive momentum (the dominant feature)
      * trend continuation (proxied by strategist's own confidence)
      * RSI in the 50–70 sweet spot (above neutral but not exhausted)

    Confidence is penalised when RSI > 70 — a Bull that buys peaks
    bleeds out. We let Bear's `rsi - 50` term do most of the work
    capturing the upside-exhaustion case; Bull just dampens slightly
    beyond 70 so it doesn't become a runaway optimist.
    """
    inputs = _extract_inputs(signal)

    # Core long-thesis score — momentum + trend, both unit-scaled.
    # Weights chosen so a clean uptrend (mom_unit=0.7, trend=0.7) lands
    # confidence ≈ 0.85, and a sideways print (mom=0.5, trend=0.5)
    # lands at 0.5 (neutral, won't fire).
    raw = 0.30 + 0.40 * inputs["momentum_unit"] + 0.30 * inputs["trend"]

    # Exhaustion penalty — RSI > 0.70 chips confidence linearly.
    if inputs["rsi"] > 0.70:
        raw -= 0.30 * (inputs["rsi"] - 0.70) / 0.30   # ~-0.30 at RSI=100

    confidence = max(0.0, min(raw, 1.0))

    # Expected_R rises with momentum magnitude — a bigger move usually
    # means a bigger continuation (until it doesn't, hence Bear).
    expected_r = 1.0 + 0.5 * abs(inputs["momentum_signed"])

    return AgentOutput(
        side="LONG",
        confidence=confidence,
        expected_r=round(expected_r, 4),
        thesis="momentum_plus_trend",
        invalidations=["rsi_divergence", "volume_drop", "regime_break"],
    )


# ── Bear agent ────────────────────────────────────────────────────────────────


def bear_agent(signal: dict[str, Any]) -> AgentOutput:
    """Profits if price goes DOWN (or sideways from a stretched level).

    Confidence rises with:
      * RSI > 70 (overbought)
      * High volatility (parabolic / overbought / oversold regimes)
      * Negative momentum

    Importantly — Bear is NOT just "inverse Bull". It explicitly
    captures the mean-reversion edge that the strategist's trend-
    following bias systematically misses.
    """
    inputs = _extract_inputs(signal)

    # Core short-thesis score:
    #   * (rsi - 0.5) → +/- 0.5 contribution at the extremes
    #   * volatility → high in parabolic regimes
    #   * momentum negation — bear wants down moves
    raw = 0.30 + 0.40 * (inputs["rsi"] - 0.30) + 0.20 * inputs["volatility"]
    raw -= 0.30 * inputs["momentum_signed"]   # positive momentum hurts bear

    # Bonus when both signals agree: high RSI + high vol = exhaustion
    # textbook. Captures the parabolic-top setup.
    if inputs["rsi"] > 0.70 and inputs["volatility"] > 0.70:
        raw += 0.15

    confidence = max(0.0, min(raw, 1.0))

    expected_r = 1.0 + 0.5 * inputs["volatility"]

    return AgentOutput(
        side="SHORT_OR_REJECT",
        confidence=confidence,
        expected_r=round(expected_r, 4),
        thesis="overbought_plus_volatility",
        invalidations=["strong_continuation", "volume_expansion_up"],
    )


# ── Options-flow narrative enrichment (Phase 2b integration hook) ─────────────
#
# Additive, never-dominant: this function appends text to the thesis
# string based on put/call ratio, but DOES NOT change confidence or
# expected_r. The Commander still resolves on score gap alone. PCR is
# evidence the analyst reads, not a vote it gets to cast.

PCR_BULL_THRESHOLD = 0.7   # < this → call-heavy, bullish option flow
PCR_BEAR_THRESHOLD = 1.3   # > this → put-heavy, bearish option flow


def apply_options_context(
    bull: AgentOutput,
    bear: AgentOutput,
    options_entry: Optional[dict],
) -> tuple[AgentOutput, AgentOutput]:
    """Enrich bull/bear theses with options-flow context.

    ``options_entry`` is a per-symbol dict from
    ``options_universe_service.read_options_snapshot`` (or ``None``).
    Returns fresh ``AgentOutput`` instances — inputs are never mutated.
    ``None`` / missing-aggregate / unset-PCR all round-trip unchanged,
    preserving the "empty-snapshot = no-op" guarantee.
    """
    import dataclasses

    if not options_entry:
        return bull, bear

    aggregate = options_entry.get("aggregate") or {}
    pcr = aggregate.get("put_call_ratio")
    stress_idx = aggregate.get("liquidity_stress_index")

    if pcr is not None:
        if pcr < PCR_BULL_THRESHOLD:
            bull = dataclasses.replace(
                bull,
                thesis=bull.thesis + " | strong_call_flow",
            )
        elif pcr > PCR_BEAR_THRESHOLD:
            bear = dataclasses.replace(
                bear,
                thesis=bear.thesis + " | elevated_put_activity",
            )

    # Liquidity stress (p90/avg spread ratio) — when this climbs into
    # the "stress" band it's a pre-volatility signal that risk is
    # building in the tails while surface-level prints look OK. Always
    # annotates the bear case because the base rate for sharp moves
    # following tail widening skews negative in equity indices. Purely
    # narrative — confidence/expected_r never move here either.
    if stress_idx is not None:
        try:
            value = float(stress_idx)
        except (TypeError, ValueError):
            value = 0.0
        if value >= 6.0:
            bear = dataclasses.replace(
                bear,
                thesis=bear.thesis + " | liquidity_instability_imminent",
            )
        elif value >= 4.0:
            bear = dataclasses.replace(
                bear,
                thesis=bear.thesis + " | liquidity_stress_rising",
            )

    return bull, bear


# ── Commander ─────────────────────────────────────────────────────────────────


def resolve_adversarial(
    bull: AgentOutput,
    bear: AgentOutput,
    *,
    edge_gap_threshold: float = EDGE_GAP_THRESHOLD,
) -> dict[str, Any]:
    """Resolve Bull vs Bear by score gap. Pure function — no Mongo,
    no clock, no env reads.

    score(side) = confidence × expected_r
    edge_gap   = bull_score - bear_score

    * gap >  threshold → LONG, with risk_multiplier = clamp(gap, 0, 1)
    * gap < -threshold → SHORT_OR_AVOID
    * else            → NO_TRADE
    """
    bull_score = bull.confidence * bull.expected_r
    bear_score = bear.confidence * bear.expected_r
    edge_gap = bull_score - bear_score

    if edge_gap > edge_gap_threshold:
        decision = "LONG"
    elif edge_gap < -edge_gap_threshold:
        decision = "SHORT_OR_AVOID"
    else:
        decision = "NO_TRADE"

    risk_multiplier = max(0.0, min(abs(edge_gap), RISK_MULTIPLIER_CAP))

    return {
        "decision": decision,
        "edge_gap": round(edge_gap, 4),
        "risk_multiplier": round(risk_multiplier, 4),
        "bull_score": round(bull_score, 4),
        "bear_score": round(bear_score, 4),
    }


# ── Gate evaluation ───────────────────────────────────────────────────────────


def _read_phase() -> str:
    """Resolve current phase from env, default ``shadow``."""
    raw = (os.environ.get(ENV_PHASE_FLAG) or "shadow").lower()
    if raw not in VALID_PHASES:
        logger.warning(
            "[adversarial] invalid %s=%r, defaulting to shadow",
            ENV_PHASE_FLAG, raw,
        )
        return "shadow"
    return raw


def _env_gate_open() -> bool:
    """First gate: env flag must be `1`. Cheap — no DB hit."""
    return os.environ.get(ENV_ENABLE_FLAG) == "1"


async def _tier3_gate_open(db: Any) -> bool:
    """Second gate: ML Tier 3 must be unlocked.

    Failures degrade closed (gate stays shut) — better to be silent
    than to fire on a stale stats read.
    """
    if db is None:
        return False
    try:
        from services.tier3_readiness import build_tier3_stats, check_tier3_unlock
        stats = await build_tier3_stats(db)
        decision = check_tier3_unlock(stats)
        return bool(decision.get("unlocked", False))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adversarial] tier3 gate check failed: %s", exc)
        return False


# ── Main entry ────────────────────────────────────────────────────────────────


async def run_adversarial_decision(
    db: Any,
    signal: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Top-level adversarial decision builder.

    Returns the decision payload on success, or ``None`` when either
    gate is closed (which is the default state today).

    Caller responsibilities:
      1. **shadow phase** — log the payload, do NOT alter the trade.
      2. **risk_only phase** — multiply position size by
         ``payload["risk_multiplier"]``. Direction stays as-is.
      3. **veto phase** — if ``decision == "NO_TRADE"``, skip the fill.
      4. **full phase** — let ``decision`` override direction.

    The phase logic is honoured by the caller, not enforced here, so
    this module stays pure and testable.
    """
    # Gate 1 — env flag (cheap, default off).
    if not _env_gate_open():
        return None

    # Gate 2 — Tier 3 unlock (DB hit, default closed on error).
    if not await _tier3_gate_open(db):
        return None

    bull = bull_agent(signal)
    bear = bear_agent(signal)
    resolution = resolve_adversarial(bull, bear)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": signal.get("symbol"),
        "regime": signal.get("regime"),
        "phase": _read_phase(),
        "bull_case": asdict(bull),
        "bear_case": asdict(bear),
        **resolution,
    }
