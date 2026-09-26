"""Alpha-native RISE AI + Sovereign decision reference.

This module accepts validated Alpha snapshots and emits advisory proposals.
It does not connect to a database, broker, worker or Core v2 executor.
Provider SHADOW/ADVISOR/PRIMARY is separate from Alpha execution authority.
Shadow results are SO; only broker-reconciled closed trades can become VE.
Live submission must separately pass operator arming, verifier, Governor and
all Core v2 broker account/position/quote/sizing/idempotency checks.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

Action = Literal['BUY', 'HOLD']
Direction = Literal['bullish', 'bearish', 'neutral']

class ProviderRole(str, Enum):
    SHADOW = 'SHADOW'
    ADVISOR = 'ADVISOR'
    PRIMARY = 'PRIMARY'
    OFFLINE = 'OFFLINE'

class ExecutionMode(str, Enum):
    OBSERVE = 'OBSERVE'
    SHADOW = 'SHADOW'
    TOEHOLD = 'TOEHOLD'
    AUTONOMOUS = 'AUTONOMOUS'
    HALT = 'HALT'

@dataclass(frozen=True)
class SovereignFeatures:
    symbol: str
    observed_at: datetime
    price: float | None = None
    rsi14: float | None = None
    macd: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    atr_pct: float | None = None
    dollar_volume: float | None = None
    spread_bps: float | None = None
    liquidity_stress: float | None = None
    flow_maturity: bool = False
    options_flow_score: float | None = None
    flow_imbalance: float | None = None
    put_call_ratio: float | None = None
    has_hot_flow: bool = False
    news_zscore: float | None = None
    sentiment_score: float | None = None
    event_risk: str = 'unknown'
    smart_money_score: float | None = None
    quiver_score: float | None = None
    rr: float | None = None
    rejection_bias: float = 0.0
    loss_streak: int = 0
    calibration_score: float | None = None

@dataclass(frozen=True)
class ModelVote:
    name: str
    direction: Direction
    score: float  # Heuristic support, never a calibrated probability.
    reason: str

@dataclass(frozen=True)
class SovereignDecision:
    symbol: str
    action: Action
    direction: Direction
    support_score: float
    size_multiplier: float  # Advisory ceiling; Core v2 sizes independently.
    reasons: tuple[str, ...]
    vetoes: tuple[str, ...]
    model_votes: tuple[ModelVote, ...]
    feature_snapshot: SovereignFeatures
    created_at: datetime
    provenance: Literal['SO'] = 'SO'

def valid(value: float | None) -> bool:
    return value is not None and math.isfinite(value)

def clamp(x: float) -> float:
    return max(0.0, min(1.0, x))

class StrategistModel:
    def predict(self, f: SovereignFeatures) -> ModelVote:
        if not all(valid(v) for v in (f.price, f.sma20, f.sma50, f.rsi14, f.macd)):
            return ModelVote('strategist', 'neutral', 0.0, 'Technical inputs missing')
        score = 0.50
        score += 0.20 if f.price > f.sma20 else -0.20
        score += 0.20 if f.sma20 > f.sma50 else -0.20
        score += 0.15 if 45 <= f.rsi14 <= 70 else 0.0
        score += 0.15 if f.macd > 0 else -0.10
        score -= 0.20 if f.rsi14 > 78 else 0.0
        score = clamp(score)
        if score >= 0.60:
            return ModelVote('strategist', 'bullish', score, 'Bullish technical structure')
        if score <= 0.40:
            return ModelVote('strategist', 'bearish', 1-score, 'Bearish technical structure')
        return ModelVote('strategist', 'neutral', 0.5, 'Mixed technical structure')

class RegimeModel:
    def predict(self, f: SovereignFeatures) -> tuple[str, str]:
        if valid(f.atr_pct) and f.atr_pct >= 0.06:
            return 'high_vol', 'High volatility'
        if all(valid(v) for v in (f.price, f.sma50, f.sma200)):
            if f.price > f.sma50 > f.sma200:
                return 'bull', 'Above major moving averages'
            if f.price < f.sma50 < f.sma200:
                return 'bear', 'Below major moving averages'
            return 'chop', 'Mixed major moving averages'
        return 'unknown', 'Regime inputs missing'

class OptionsIntentModel:
    def predict(self, f: SovereignFeatures) -> ModelVote:
        if not (f.has_hot_flow and f.flow_maturity and valid(f.flow_imbalance)
                and valid(f.put_call_ratio)):
            return ModelVote('options_intent', 'neutral', 0.0, 'Options flow unavailable')
        score = clamp(0.50 + 0.35*f.flow_imbalance +
                      (0.10 if f.put_call_ratio < 0.75 else
                       -0.10 if f.put_call_ratio > 1.30 else 0.0))
        if score >= 0.60:
            return ModelVote('options_intent', 'bullish', score, 'Call-side pressure')
        if score <= 0.40:
            return ModelVote('options_intent', 'bearish', 1-score, 'Put-side pressure')
        return ModelVote('options_intent', 'neutral', 0.5, 'Balanced flow')

class CatalystModel:
    def predict(self, f: SovereignFeatures) -> ModelVote:
        if f.event_risk == 'restricted':
            return ModelVote('catalyst', 'neutral', 0.0, 'Restricted event')
        if not (valid(f.news_zscore) and valid(f.sentiment_score)):
            return ModelVote('catalyst', 'neutral', 0.0, 'Catalyst unavailable')
        if abs(f.news_zscore) >= 4:
            return ModelVote('catalyst', 'neutral', 0.0, 'News shock')
        if f.news_zscore >= 2.5 and f.sentiment_score > 0.25:
            return ModelVote('catalyst', 'bullish', clamp(0.55+f.sentiment_score), 'Positive catalyst')
        if f.news_zscore >= 2.5 and f.sentiment_score < -0.25:
            return ModelVote('catalyst', 'bearish', clamp(0.55-f.sentiment_score), 'Negative catalyst')
        return ModelVote('catalyst', 'neutral', 0.5, 'No strong catalyst')

def evaluate(f: SovereignFeatures, *, now: datetime | None = None) -> SovereignDecision:
    """Long-entry proposal only; SELL/short/exit need distinct broker contracts."""
    now = now or datetime.now(timezone.utc)
    vetoes: list[str] = []
    if not f.symbol.strip():
        vetoes.append('MISSING_SYMBOL')
    if f.observed_at.tzinfo is None or now.tzinfo is None:
        vetoes.append('UNVERIFIED_TIMESTAMP')
    elif not 0 <= (now-f.observed_at).total_seconds() <= 60:
        vetoes.append('STALE_FEATURE_SNAPSHOT')
    for field in ('price', 'dollar_volume', 'spread_bps', 'rr'):
        if not valid(getattr(f, field)):
            vetoes.append('MISSING_'+field.upper())
    if valid(f.price) and f.price <= 0:
        vetoes.append('INVALID_PRICE')
    if valid(f.dollar_volume) and f.dollar_volume < 2_000_000:
        vetoes.append('LOW_DOLLAR_VOLUME')
    if valid(f.spread_bps) and (f.spread_bps < 0 or f.spread_bps > 75):
        vetoes.append('WIDE_OR_INVALID_SPREAD')
    if valid(f.rr) and f.rr < 1.5:
        vetoes.append('LOW_RR')
    if f.event_risk not in ('normal', 'elevated'):
        vetoes.append('UNKNOWN_OR_RESTRICTED_EVENT_RISK')
    if valid(f.news_zscore) and abs(f.news_zscore) >= 4:
        vetoes.append('NEWS_SHOCK')
    if valid(f.liquidity_stress) and f.liquidity_stress >= 6:
        vetoes.append('LIQUIDITY_STRESS')
    if f.loss_streak >= 4 or not valid(f.rejection_bias) or f.rejection_bias > 0.50:
        vetoes.append('HISTORICAL_RISK')
    votes = (StrategistModel().predict(f), OptionsIntentModel().predict(f),
             CatalystModel().predict(f))
    regime, regime_reason = RegimeModel().predict(f)
    if regime in ('unknown', 'high_vol', 'bear'):
        vetoes.append('UNSUITABLE_LONG_REGIME')
    if votes[0].direction != 'bullish':
        vetoes.append('STRATEGIST_NOT_BULLISH')
    if any(v.direction == 'bearish' for v in votes[1:]):
        vetoes.append('CONTRADICTORY_VOTE')
    aligned = [v.score for v in votes if v.direction == 'bullish']
    support = min(aligned) if aligned else 0.0
    if len(aligned) < 2:
        vetoes.append('INSUFFICIENT_INDEPENDENT_SUPPORT')
    if support < 0.70:
        vetoes.append('SUPPORT_BELOW_THRESHOLD')
    size = 0.0 if vetoes else (0.5 if support < 0.85 else 1.0)
    if f.event_risk == 'elevated':
        size *= 0.75
    if valid(f.liquidity_stress) and f.liquidity_stress >= 4:
        size *= 0.70
    return SovereignDecision(
        symbol=f.symbol.upper().strip(), action='HOLD' if vetoes else 'BUY',
        direction='neutral' if vetoes else 'bullish', support_score=round(support, 4),
        size_multiplier=round(size, 4), reasons=(regime_reason, *(v.reason for v in votes)),
        vetoes=tuple(dict.fromkeys(vetoes)), model_votes=votes,
        feature_snapshot=f, created_at=now)

@dataclass(frozen=True)
class Authority:
    mode: ExecutionMode
    operator_live_armed: bool = False
    verifier_passed: bool = False
    governor_passed: bool = False
    kill_switch_active: bool = False

def proposal_may_enter_core_v2(decision: SovereignDecision, authority: Authority) -> bool:
    """Advisory preflight, NOT an order authorization or Core v2 gate substitute."""
    return (decision.action == 'BUY' and not decision.vetoes and
            authority.mode in (ExecutionMode.TOEHOLD, ExecutionMode.AUTONOMOUS) and
            authority.operator_live_armed and authority.verifier_passed and
            authority.governor_passed and not authority.kill_switch_active)

def decision_record(d: SovereignDecision) -> dict:
    """Compact local store record; no Mongo requirement."""
    return {'symbol': d.symbol, 'action': d.action, 'support_score': d.support_score,
            'vetoes': d.vetoes, 'reasons': d.reasons,
            'created_at': d.created_at.isoformat(), 'provenance': 'SO'}

# Production integration must add: verified Alpha snapshot adapter,
# independent verifier, durable operator authority, Core v2 handoff,
# broker reconciliation, open-position management and audited outcome storage.
# A shadow outcome is never a broker-verified execution record (VE).
