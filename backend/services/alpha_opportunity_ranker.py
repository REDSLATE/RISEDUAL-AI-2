"""RISEDUAL Alpha opportunity comparator.

Extracted/adapted from Foundation v2.3's scan -> discern -> rank -> act design.
This module is deliberately execution-agnostic: it MUST NOT place orders and it
MUST NOT import/replace Public short execution code.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

ACTIONABLE = "ACTIONABLE"


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


@dataclass(frozen=True)
class OpportunityWeights:
    # Preserve Foundation discernment as the dominant input, but make the
    # final capital-allocation decision sensitive to execution quality.
    discernment: float = 0.50
    learned_edge: float = 0.16
    entry_quality: float = 0.12
    liquidity: float = 0.08
    market_alignment: float = 0.08
    relative_strength: float = 0.06
    chase_penalty: float = 0.14
    execution_risk_penalty: float = 0.12
    borrow_cost_penalty: float = 0.10


@dataclass(frozen=True)
class RankedOpportunity:
    signal: Any
    opportunity_score: float
    components: Mapping[str, float] = field(default_factory=dict)


def compute_opportunity_score(
    signal: Any,
    *,
    learned_edge: float | None = None,
    entry_quality: float = 1.0,
    liquidity: float = 1.0,
    market_alignment: float = 0.5,
    relative_strength: float = 0.5,
    chase_risk: float = 0.0,
    execution_risk: float = 0.0,
    borrow_cost_risk: float = 0.0,
    weights: OpportunityWeights = OpportunityWeights(),
) -> tuple[float, dict[str, float]]:
    """Return a direction-neutral 0..1 capital-allocation score.

    SELL_SHORT and BUY are intentionally comparable. Borrow-cost risk should be
    zero for longs and populated for shorts when Public supplies HTB/locate cost.
    """
    discernment = _clamp(getattr(signal, "discernment_score", getattr(signal, "confidence", 0.0)))
    if learned_edge is None:
        # Existing Alpha/Foundation edge scores may be signed. Normalize a
        # conservative [-0.25,+0.25] modifier into [0,1].
        raw_edge = float(getattr(signal, "edge_score", 0.0) or 0.0)
        edge01 = _clamp((max(-0.25, min(0.25, raw_edge)) + 0.25) / 0.50)
    else:
        edge01 = _clamp(learned_edge)

    vals = {
        "discernment": discernment,
        "learned_edge": edge01,
        "entry_quality": _clamp(entry_quality),
        "liquidity": _clamp(liquidity),
        "market_alignment": _clamp(market_alignment),
        "relative_strength": _clamp(relative_strength),
        "chase_risk": _clamp(chase_risk),
        "execution_risk": _clamp(execution_risk),
        "borrow_cost_risk": _clamp(borrow_cost_risk),
    }
    positive = (
        weights.discernment * vals["discernment"]
        + weights.learned_edge * vals["learned_edge"]
        + weights.entry_quality * vals["entry_quality"]
        + weights.liquidity * vals["liquidity"]
        + weights.market_alignment * vals["market_alignment"]
        + weights.relative_strength * vals["relative_strength"]
    )
    penalty = (
        weights.chase_penalty * vals["chase_risk"]
        + weights.execution_risk_penalty * vals["execution_risk"]
        + weights.borrow_cost_penalty * vals["borrow_cost_risk"]
    )
    return _clamp(positive - penalty), vals


def rank_actionable(
    candidates: Iterable[Any],
    *,
    context_by_symbol: Mapping[str, Mapping[str, float]] | None = None,
    min_score: float = 0.0,
) -> list[RankedOpportunity]:
    """Rank ACTIONABLE long and short candidates best-first.

    WATCH/REJECT/HOLD never compete for capital. This function performs no
    broker eligibility check and no execution; downstream gates remain intact.
    """
    context_by_symbol = context_by_symbol or {}
    ranked: list[RankedOpportunity] = []
    for signal in candidates:
        if _enum_value(getattr(signal, "actionability", "")) != ACTIONABLE:
            continue
        if _enum_value(getattr(signal, "direction", "HOLD")) == "HOLD":
            continue
        symbol = str(getattr(signal, "symbol", ""))
        ctx = dict(context_by_symbol.get(symbol, {}))
        score, components = compute_opportunity_score(signal, **ctx)
        if score >= min_score:
            ranked.append(RankedOpportunity(signal, score, components))
    ranked.sort(key=lambda item: item.opportunity_score, reverse=True)
    return ranked
