"""
Alpha — Standalone Decision Engine
===================================
A self-contained, copy-pasteable distillation of the RISEDUAL Alpha trading
brain. No Mongo, no FastAPI, no env vars, no network calls. Pure functions
+ one orchestrator class. Drop this file anywhere, feed it indicators,
get back LONG / SHORT / NO_TRADE with sized risk.

What's inside
-------------
1. Bull / Bear adversarial agents — one is always wrong by construction.
2. Commander — resolves the conflict by score gap (confidence × expected_R).
3. Optional narrative enrichment (options flow PCR, catalyst sentiment).
4. Toxic-spike seal — confidence is hard-capped at 0.95 across the pipeline.
5. `Alpha` orchestrator — feed it a signal dict, get the full decision.

Why two agents?
---------------
Consensus systems (Strategist + Auditor) share an objective, so when both
agree and the trade fails you can't tell which one was wrong. With Bull
vs Bear, the loser is unambiguous after the trade resolves — perfect
ground truth for online learning.

Usage
-----
    from alpha_standalone import Alpha

    alpha = Alpha()
    decision = alpha.decide({
        "regime": "trending",
        "strategist": {
            "confidence": 0.72,                 # 0–1 — your own model
            "indicators": {
                "rsi": 63.0,                    # 0–100
                "ema20": 195.40,                # informational
                "momentum_5b": 0.024,           # signed % over 5 bars
            },
        },
        "auditor": {"confidence": 0.65},        # optional, 0–1
        # Optional enrichers — never move the score, just annotate thesis:
        "options": {"aggregate": {"put_call_ratio": 0.55,
                                   "liquidity_stress_index": 2.1}},
        "catalyst": {"news_shock": {"sentiment_label": "bullish",
                                     "shock_state": "calm",
                                     "latest_headline": "Beats EPS by 12%"}},
    })
    print(decision)
    # {
    #   "decision": "LONG",
    #   "edge_gap": 0.42,
    #   "risk_multiplier": 0.42,
    #   "bull": {...}, "bear": {...},
    #   "confidence": 0.78,     # already capped at 0.95
    #   "size_fraction": 0.42,  # multiply this into your portfolio % per trade
    #   "thesis": "...",
    # }

License: MIT. Do whatever you want with it.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Tunables — all the knobs Alpha exposes. Override via `AlphaConfig` if needed.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AlphaConfig:
    # Decision threshold on edge_gap = bull_score - bear_score.
    # If |gap| < edge_gap_threshold → NO_TRADE.
    edge_gap_threshold: float = 0.35

    # Risk multiplier ceiling — multiplier ∈ [0, ceiling].
    risk_multiplier_cap: float = 1.0

    # TOXIC-SPIKE SEAL — hard ceiling on confidence anywhere in the
    # pipeline. 100%-confidence failures poison memory stores, so we
    # clamp at 0.95 and log when the cap fires.
    confidence_cap: float = 0.95

    # PCR thresholds for narrative enrichment (does NOT move scores).
    pcr_bull_threshold: float = 0.7
    pcr_bear_threshold: float = 1.3

    # Minimum confidence floor — Alpha will return NO_TRADE if the
    # winning side scores below this even when the gap is wide.
    min_confidence_floor: float = 0.55


# ─────────────────────────────────────────────────────────────────────────────
# Pure dataclass — JSON-serialisable.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AgentOutput:
    side: str
    confidence: float
    expected_r: float
    thesis: str
    invalidations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Confidence cap — the toxic-spike seal. Use this everywhere a 0–1 or 0–100
# confidence number leaves the pipeline.
# ─────────────────────────────────────────────────────────────────────────────


def cap_confidence(value: float, *, cap: float = 0.95) -> float:
    """Hard-cap any confidence number at `cap`. Accepts 0–1 or 0–100;
    returns same scale. Anything ≥ cap is collapsed to cap exactly.

    Why: A 100%-confidence failure is a sigmoid saturation, not real
    certainty. Letting them through poisons the toxic-lesson memory
    store (Chroma keeps re-surfacing them as "this looks identical to
    what worked last time"). Cap visible, cap honest.
    """
    if value is None:
        return 0.0
    v = float(value)
    # Detect scale: anything >1 is 0–100.
    if v > 1.0:
        ceiling = cap * 100.0
    else:
        ceiling = cap
    if v >= ceiling:
        return ceiling
    if v < 0.0:
        return 0.0
    return v


# ─────────────────────────────────────────────────────────────────────────────
# Input normalisation — squash every raw indicator into a 0–1 range so the
# heuristic weights actually move with the data.
# ─────────────────────────────────────────────────────────────────────────────


_VOL_BY_REGIME = {
    "parabolic": 0.85,
    "overbought": 0.75,
    "oversold": 0.75,
    "trending": 0.50,
    "trend_up": 0.50,
    "trend_down": 0.50,
    "range": 0.30,
    "uncertain": 0.40,
}


def _extract_inputs(signal: dict[str, Any]) -> dict[str, float]:
    """Pull indicators out of the signal dict and normalise to 0–1.

    Expected signal shape::

        {
            "regime": "trending" | "parabolic" | ...,
            "strategist": {
                "confidence": 0.72,                  # 0–1
                "indicators": {
                    "rsi": 63.0,                    # 0–100
                    "momentum_5b": 0.024,           # signed % over 5 bars
                },
            },
            "auditor": {"confidence": 0.65},        # optional
        }
    """
    strategist = signal.get("strategist") or {}
    indicators = strategist.get("indicators") or {}
    rsi = float(indicators.get("rsi") or 50.0)
    momentum_raw = float(indicators.get("momentum_5b") or 0.0)
    regime = (signal.get("regime") or "").lower()
    auditor = signal.get("auditor") or {}

    # rsi → 0–1
    rsi_norm = max(0.0, min(rsi / 100.0, 1.0))

    # Momentum: tanh-squash a 5-bar % move. Scale=20 so a +5% bar
    # lands ~0.76, +2.5% lands ~0.46, -2.5% lands ~-0.46.
    momentum_signed = math.tanh(momentum_raw * 20.0)   # [-1, 1]
    momentum_unit = (momentum_signed + 1.0) / 2.0      # [0, 1]; 0.5 = neutral

    # Trend proxy: reuse strategist's own confidence (already encodes
    # price-vs-EMA distance). Saves us recomputing from raw price.
    trend_proxy = float(strategist.get("confidence") or 0.5)

    # Volatility proxy: derived from regime. Parabolic = high vol,
    # range = low. Used by Bear which feeds on mean-reversion.
    volatility = _VOL_BY_REGIME.get(regime, 0.40)

    return {
        "rsi": rsi_norm,
        "momentum_signed": momentum_signed,
        "momentum_unit": momentum_unit,
        "trend": trend_proxy,
        "volatility": volatility,
        "auditor_conf": float(auditor.get("confidence") or 0.5),
        "regime": regime,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Bull agent — profits if price goes UP. Ignores reversal risk by design.
# ─────────────────────────────────────────────────────────────────────────────


def bull_agent(signal: dict[str, Any], *, cfg: AlphaConfig) -> AgentOutput:
    inputs = _extract_inputs(signal)

    # Core long-thesis score — momentum + trend, unit-scaled. A clean
    # uptrend (mom_unit=0.7, trend=0.7) lands ~0.85; a sideways print
    # (0.5, 0.5) lands at 0.5 (won't fire).
    raw = 0.30 + 0.40 * inputs["momentum_unit"] + 0.30 * inputs["trend"]

    # Exhaustion penalty — RSI > 0.70 chips confidence linearly so
    # Bull doesn't become a runaway optimist at the peak.
    if inputs["rsi"] > 0.70:
        raw -= 0.30 * (inputs["rsi"] - 0.70) / 0.30   # ~-0.30 at RSI=100

    confidence = cap_confidence(max(0.0, min(raw, 1.0)), cap=cfg.confidence_cap)

    # Expected_R rises with momentum magnitude.
    expected_r = 1.0 + 0.5 * abs(inputs["momentum_signed"])

    return AgentOutput(
        side="LONG",
        confidence=confidence,
        expected_r=round(expected_r, 4),
        thesis="momentum_plus_trend",
        invalidations=["rsi_divergence", "volume_drop", "regime_break"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bear agent — profits if price goes DOWN (or sideways from a stretched level).
# NOT just "inverse Bull" — captures the mean-reversion edge that trend-
# following systematically misses.
# ─────────────────────────────────────────────────────────────────────────────


def bear_agent(signal: dict[str, Any], *, cfg: AlphaConfig) -> AgentOutput:
    inputs = _extract_inputs(signal)

    raw = 0.30 + 0.40 * (inputs["rsi"] - 0.30) + 0.20 * inputs["volatility"]
    raw -= 0.30 * inputs["momentum_signed"]   # positive momentum hurts bear

    # Bonus: high RSI + high vol = textbook parabolic-top exhaustion.
    if inputs["rsi"] > 0.70 and inputs["volatility"] > 0.70:
        raw += 0.15

    confidence = cap_confidence(max(0.0, min(raw, 1.0)), cap=cfg.confidence_cap)
    expected_r = 1.0 + 0.5 * inputs["volatility"]

    return AgentOutput(
        side="SHORT_OR_REJECT",
        confidence=confidence,
        expected_r=round(expected_r, 4),
        thesis="overbought_plus_volatility",
        invalidations=["strong_continuation", "volume_expansion_up"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Narrative enrichment — additive only. Annotates thesis strings, never
# moves confidence or expected_R. The Commander resolves on score alone.
# ─────────────────────────────────────────────────────────────────────────────


def apply_options_context(
    bull: AgentOutput,
    bear: AgentOutput,
    options_entry: Optional[dict],
    *,
    cfg: AlphaConfig,
) -> tuple[AgentOutput, AgentOutput]:
    if not options_entry:
        return bull, bear
    agg = options_entry.get("aggregate") or {}
    pcr = agg.get("put_call_ratio")
    stress = agg.get("liquidity_stress_index")

    if pcr is not None:
        if pcr < cfg.pcr_bull_threshold:
            bull = dataclasses.replace(bull, thesis=bull.thesis + " | strong_call_flow")
        elif pcr > cfg.pcr_bear_threshold:
            bear = dataclasses.replace(bear, thesis=bear.thesis + " | elevated_put_activity")

    if stress is not None:
        try:
            v = float(stress)
        except (TypeError, ValueError):
            v = 0.0
        if v >= 6.0:
            bear = dataclasses.replace(bear, thesis=bear.thesis + " | liquidity_instability_imminent")
        elif v >= 4.0:
            bear = dataclasses.replace(bear, thesis=bear.thesis + " | liquidity_stress_rising")

    return bull, bear


def apply_catalyst_context(
    bull: AgentOutput,
    bear: AgentOutput,
    catalyst: Optional[dict],
) -> tuple[AgentOutput, AgentOutput]:
    if not catalyst:
        return bull, bear
    shock = catalyst.get("news_shock", {}) or {}
    sentiment = shock.get("sentiment_label")
    state = shock.get("shock_state")
    z = shock.get("news_zscore")

    if sentiment == "bullish":
        bull = dataclasses.replace(bull, thesis=bull.thesis + " | bullish_catalyst_sentiment")
    elif sentiment == "bearish":
        bear = dataclasses.replace(bear, thesis=bear.thesis + " | bearish_catalyst_sentiment")

    if state in {"elevated", "high"}:
        tag = f"news_shock_{state}" + (f"_z{z}" if z is not None else "")
        bull = dataclasses.replace(bull, thesis=bull.thesis + f" | {tag}")
        bear = dataclasses.replace(bear, thesis=bear.thesis + f" | {tag}")

    return bull, bear


# ─────────────────────────────────────────────────────────────────────────────
# Commander — resolves Bull vs Bear by score gap. Pure function.
# ─────────────────────────────────────────────────────────────────────────────


def resolve_adversarial(
    bull: AgentOutput,
    bear: AgentOutput,
    *,
    cfg: AlphaConfig,
) -> dict[str, Any]:
    """score(side) = confidence × expected_r
    edge_gap = bull_score - bear_score

      * gap >  threshold → LONG, with risk_multiplier = clamp(gap)
      * gap < -threshold → SHORT_OR_AVOID
      * else            → NO_TRADE
    """
    bull_score = bull.confidence * bull.expected_r
    bear_score = bear.confidence * bear.expected_r
    edge_gap = bull_score - bear_score

    if edge_gap > cfg.edge_gap_threshold:
        decision = "LONG"
        winning_conf = bull.confidence
    elif edge_gap < -cfg.edge_gap_threshold:
        decision = "SHORT_OR_AVOID"
        winning_conf = bear.confidence
    else:
        decision = "NO_TRADE"
        winning_conf = max(bull.confidence, bear.confidence)

    # Confidence floor — even with a wide gap, if the winner is below
    # the floor we don't fire. Stops Alpha from acting on weak signals.
    if decision != "NO_TRADE" and winning_conf < cfg.min_confidence_floor:
        decision = "NO_TRADE"

    risk_multiplier = max(0.0, min(abs(edge_gap), cfg.risk_multiplier_cap))

    return {
        "decision": decision,
        "edge_gap": round(edge_gap, 4),
        "risk_multiplier": round(risk_multiplier, 4),
        "bull_score": round(bull_score, 4),
        "bear_score": round(bear_score, 4),
        "confidence": round(cap_confidence(winning_conf, cap=cfg.confidence_cap), 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Alpha — orchestrator. The class you actually instantiate.
# ─────────────────────────────────────────────────────────────────────────────


class Alpha:
    """The full Alpha decision pipeline as one object.

    Stateless by design — every `.decide()` call is independent. If you
    want online learning, wrap this and persist outcomes elsewhere.
    """

    def __init__(self, config: Optional[AlphaConfig] = None) -> None:
        self.cfg = config or AlphaConfig()

    def decide(self, signal: dict[str, Any]) -> dict[str, Any]:
        """End-to-end decision. Returns a JSON-serialisable dict.

        Required signal keys: ``regime``, ``strategist.indicators.rsi``,
        ``strategist.indicators.momentum_5b``, ``strategist.confidence``.

        Optional: ``auditor.confidence``, ``options``, ``catalyst``.
        """
        bull = bull_agent(signal, cfg=self.cfg)
        bear = bear_agent(signal, cfg=self.cfg)

        bull, bear = apply_options_context(bull, bear, signal.get("options"), cfg=self.cfg)
        bull, bear = apply_catalyst_context(bull, bear, signal.get("catalyst"))

        verdict = resolve_adversarial(bull, bear, cfg=self.cfg)

        # Compose the final response. `size_fraction` is the number you
        # multiply into your per-trade portfolio % to size the position.
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision": verdict["decision"],
            "confidence": verdict["confidence"],
            "size_fraction": verdict["risk_multiplier"],
            "edge_gap": verdict["edge_gap"],
            "bull": bull.as_dict(),
            "bear": bear.as_dict(),
            "thesis": (bull.thesis if verdict["decision"] == "LONG"
                       else bear.thesis if verdict["decision"] == "SHORT_OR_AVOID"
                       else "no_edge"),
            "config": dataclasses.asdict(self.cfg),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Demo — run `python alpha_standalone.py` to see Alpha in action.
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import json

    alpha = Alpha()

    print("\n── Sample 1: clean uptrend (expect LONG) ──")
    print(json.dumps(alpha.decide({
        "regime": "trending",
        "strategist": {
            "confidence": 0.75,
            "indicators": {"rsi": 62.0, "ema20": 195.40, "momentum_5b": 0.028},
        },
        "auditor": {"confidence": 0.70},
        "catalyst": {"news_shock": {"sentiment_label": "bullish",
                                     "shock_state": "calm",
                                     "latest_headline": "Beats EPS by 12%"}},
    }), indent=2))

    print("\n── Sample 2: parabolic top (expect SHORT_OR_AVOID) ──")
    print(json.dumps(alpha.decide({
        "regime": "parabolic",
        "strategist": {
            "confidence": 0.60,
            "indicators": {"rsi": 84.0, "ema20": 220.0, "momentum_5b": 0.005},
        },
        "options": {"aggregate": {"put_call_ratio": 1.42,
                                   "liquidity_stress_index": 5.1}},
    }), indent=2))

    print("\n── Sample 3: choppy range (expect NO_TRADE) ──")
    print(json.dumps(alpha.decide({
        "regime": "range",
        "strategist": {
            "confidence": 0.52,
            "indicators": {"rsi": 51.0, "ema20": 100.0, "momentum_5b": 0.001},
        },
    }), indent=2))
