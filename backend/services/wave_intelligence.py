"""MTR-inspired market-state machine for Mission Control observation.

This module is deliberately outside the execution authority path. It reads
closed OHLCV bars, classifies the market, and emits immutable diagnostic
context. It cannot create opinions or intents, change scores or sizing, block
RoadGuard, or call a broker.

The small process-local state cache exists only for mode hysteresis. Replaying
the same bars through a fresh ``WaveIntelligenceMachine`` produces the same
observations, and evaluating the same source bar twice is idempotent.
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence

MODEL_VERSION = "wave-intelligence-v1"
AUTHORITY = "OBSERVE_ONLY"


class WaveMode(str, Enum):
    WAIT = "WAIT"
    TREND_FOLLOW = "TREND_FOLLOW"
    RANGE_GRID = "RANGE_GRID"
    DANGER_PAUSE = "DANGER_PAUSE"


class WaveBias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True, slots=True)
class WaveConfig:
    min_bars: int = 20
    fast_ema_period: int = 8
    slow_ema_period: int = 21
    atr_period: int = 14
    regime_lookback: int = 20
    trend_enter: float = 0.62
    trend_exit: float = 0.52
    range_enter: float = 0.60
    range_exit: float = 0.52
    danger_enter: float = 0.72
    danger_exit: float = 0.50
    score_margin: float = 0.08
    cooldown_bars: int = 2
    max_symbols: int = 512


@dataclass(frozen=True, slots=True)
class WaveScores:
    trend: float
    range: float
    danger: float
    efficiency: float
    ema_separation_atr: float
    donchian_edge: float
    volatility_ratio: float
    shock_atr: float


@dataclass(frozen=True, slots=True)
class WaveObservation:
    observation_id: str
    model_version: str
    authority: str
    symbol: str
    lane: str
    timeframe: str
    as_of: str
    bars_seen: int
    data_quality: str
    mode: WaveMode
    previous_mode: WaveMode
    mode_since: str
    cooldown_remaining: int
    bias: WaveBias
    scores: WaveScores
    atr: float
    grid_step_price: float
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict:
        """Return a compact, serialization-safe observation payload."""
        return {
            "observation_id": self.observation_id,
            "model_version": self.model_version,
            "authority": self.authority,
            "can_execute": False,
            "can_size": False,
            "can_block": False,
            "symbol": self.symbol,
            "lane": self.lane,
            "timeframe": self.timeframe,
            "as_of": self.as_of,
            "bars_seen": self.bars_seen,
            "data_quality": self.data_quality,
            "mode": self.mode.value,
            "previous_mode": self.previous_mode.value,
            "mode_since": self.mode_since,
            "cooldown_remaining": self.cooldown_remaining,
            "bias": self.bias.value,
            "scores": {
                "trend": self.scores.trend,
                "range": self.scores.range,
                "danger": self.scores.danger,
                "efficiency": self.scores.efficiency,
                "ema_separation_atr": self.scores.ema_separation_atr,
                "donchian_edge": self.scores.donchian_edge,
                "volatility_ratio": self.scores.volatility_ratio,
                "shock_atr": self.scores.shock_atr,
            },
            "atr": self.atr,
            "grid_step_price": self.grid_step_price,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(slots=True)
class _MachineState:
    mode: WaveMode = WaveMode.WAIT
    mode_since: str = ""
    cooldown_remaining: int = 0
    last_bar_at: str = ""
    last_observation: Optional[WaveObservation] = None


@dataclass(frozen=True, slots=True)
class _NormalizedBars:
    opens: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]
    closes: tuple[float, ...]
    as_of: str


class WaveIntelligenceMachine:
    """Bounded market-mode state machine with closed-bar idempotency."""

    def __init__(self, config: Optional[WaveConfig] = None) -> None:
        self.config = config or WaveConfig()
        self._states: OrderedDict[str, _MachineState] = OrderedDict()

    def clear(self) -> None:
        self._states.clear()

    def evaluate(
        self,
        *,
        symbol: str,
        lane: str,
        timeframe: str,
        bars: Sequence[Mapping[str, object]],
        spread_bps: Optional[float] = None,
    ) -> WaveObservation:
        sym = (symbol or "").strip().upper()
        lane_lc = (lane or "").strip().lower()
        if not sym:
            raise ValueError("symbol required")
        if lane_lc not in {"equity", "crypto"}:
            raise ValueError("lane must be equity or crypto")

        normalized = _normalize_bars(bars)
        key = f"{lane_lc}:{sym}:{timeframe or 'unknown'}"
        state = self._states.get(key)
        if state is not None and state.last_bar_at == normalized.as_of:
            if state.last_observation is not None:
                self._states.move_to_end(key)
                return state.last_observation

        is_new = state is None
        if state is None:
            state = _MachineState(mode_since=normalized.as_of)
            self._states[key] = state
            self._trim_state_cache()
        else:
            self._states.move_to_end(key)

        scores, atr, bias, score_reasons = _score_bars(
            normalized,
            config=self.config,
            spread_bps=spread_bps,
        )
        data_quality = (
            "READY"
            if len(normalized.closes) >= self.config.min_bars
            else "INSUFFICIENT"
        )
        candidate = _candidate_mode(scores, data_quality, self.config)
        previous = state.mode
        mode, transition_reason = self._transition(
            state=state,
            candidate=candidate,
            scores=scores,
            as_of=normalized.as_of,
            is_new=is_new,
        )

        close = normalized.closes[-1] if normalized.closes else 0.0
        grid_step = max(close * 0.0025, atr * 0.35) if close > 0 else 0.0
        reasons = _dedupe_codes(
            (
                f"WAVE_MODE_{mode.value}",
                transition_reason,
                *score_reasons,
            )
        )
        observation_id = _observation_id(
            key=key,
            as_of=normalized.as_of,
            mode=mode,
            scores=scores,
        )
        observation = WaveObservation(
            observation_id=observation_id,
            model_version=MODEL_VERSION,
            authority=AUTHORITY,
            symbol=sym,
            lane=lane_lc,
            timeframe=timeframe or "unknown",
            as_of=normalized.as_of,
            bars_seen=len(normalized.closes),
            data_quality=data_quality,
            mode=mode,
            previous_mode=previous,
            mode_since=state.mode_since,
            cooldown_remaining=state.cooldown_remaining,
            bias=bias,
            scores=scores,
            atr=_round(atr),
            grid_step_price=_round(grid_step),
            reason_codes=reasons,
        )
        state.last_bar_at = normalized.as_of
        state.last_observation = observation
        return observation

    def _transition(
        self,
        *,
        state: _MachineState,
        candidate: WaveMode,
        scores: WaveScores,
        as_of: str,
        is_new: bool,
    ) -> tuple[WaveMode, str]:
        current = state.mode
        desired = candidate

        if current == WaveMode.DANGER_PAUSE and scores.danger > self.config.danger_exit:
            desired = current
        elif (
            current == WaveMode.TREND_FOLLOW
            and scores.trend >= self.config.trend_exit
            and scores.danger < self.config.danger_enter
        ):
            desired = current
        elif (
            current == WaveMode.RANGE_GRID
            and scores.range >= self.config.range_exit
            and scores.danger < self.config.danger_enter
        ):
            desired = current

        danger_preempts = candidate == WaveMode.DANGER_PAUSE
        if desired != current and state.cooldown_remaining > 0 and not danger_preempts:
            state.cooldown_remaining -= 1
            return current, "WAVE_COOLDOWN_HOLD"

        if desired != current:
            state.mode = desired
            state.mode_since = as_of
            state.cooldown_remaining = self.config.cooldown_bars
            return desired, f"WAVE_TRANSITION_{current.value}_TO_{desired.value}"

        if not is_new and state.cooldown_remaining > 0:
            state.cooldown_remaining -= 1
        return current, "WAVE_MODE_HELD"

    def _trim_state_cache(self) -> None:
        while len(self._states) > self.config.max_symbols:
            self._states.popitem(last=False)


def summarize_wave_observations(
    contexts: Iterable[Mapping[str, object]],
    *,
    danger_symbol_limit: int = 8,
) -> dict:
    """Build the compact pulse-receipt view without per-brain duplication."""
    observations = [c for c in contexts if c and c.get("authority") == AUTHORITY]
    if not observations:
        return {}

    mode_counts = {mode.value: 0 for mode in WaveMode}
    lane_counts: dict[str, dict[str, int]] = {}
    danger_rows: list[tuple[float, str]] = []
    max_danger = 0.0
    insufficient = 0
    versions: set[str] = set()
    for context in observations:
        mode = str(context.get("mode") or WaveMode.WAIT.value)
        lane = str(context.get("lane") or "unknown")
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        lane_modes = lane_counts.setdefault(lane, {})
        lane_modes[mode] = lane_modes.get(mode, 0) + 1
        if context.get("data_quality") != "READY":
            insufficient += 1
        version = str(context.get("model_version") or "")
        if version:
            versions.add(version)
        scores = context.get("scores")
        danger = 0.0
        if isinstance(scores, Mapping):
            danger = _safe_float(scores.get("danger")) or 0.0
        max_danger = max(max_danger, danger)
        if mode == WaveMode.DANGER_PAUSE.value:
            danger_rows.append((danger, str(context.get("symbol") or "")))

    danger_rows.sort(key=lambda row: (-row[0], row[1]))
    return {
        "authority": AUTHORITY,
        "observations": len(observations),
        "insufficient": insufficient,
        "mode_counts": mode_counts,
        "lane_mode_counts": lane_counts,
        "danger_symbols": [s for _, s in danger_rows[:danger_symbol_limit] if s],
        "max_danger_score": _round(max_danger),
        "model_versions": sorted(versions),
    }


def _normalize_bars(bars: Sequence[Mapping[str, object]]) -> _NormalizedBars:
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    timestamps: list[str] = []
    for bar in bars or ():
        close = _first_float(bar, "c", "close")
        if close is None or close <= 0:
            continue
        open_price = _first_float(bar, "o", "open")
        high = _first_float(bar, "h", "high")
        low = _first_float(bar, "l", "low")
        open_price = close if open_price is None or open_price <= 0 else open_price
        high = max(close, open_price) if high is None else max(high, close, open_price)
        low = min(close, open_price) if low is None else min(low, close, open_price)
        opens.append(open_price)
        highs.append(high)
        lows.append(low)
        closes.append(close)
        timestamps.append(str(bar.get("ts") or bar.get("timestamp") or len(closes)))
    as_of = timestamps[-1] if timestamps else ""
    return _NormalizedBars(
        opens=tuple(opens),
        highs=tuple(highs),
        lows=tuple(lows),
        closes=tuple(closes),
        as_of=as_of,
    )


def _score_bars(
    bars: _NormalizedBars,
    *,
    config: WaveConfig,
    spread_bps: Optional[float],
) -> tuple[WaveScores, float, WaveBias, tuple[str, ...]]:
    count = len(bars.closes)
    if count < config.min_bars:
        zero = WaveScores(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return zero, 0.0, WaveBias.NEUTRAL, ("WAVE_INSUFFICIENT_BARS",)

    closes = bars.closes
    true_ranges = _true_ranges(bars)
    atr_values = true_ranges[-min(config.atr_period, len(true_ranges)) :]
    atr = sum(atr_values) / len(atr_values) if atr_values else 0.0
    atr_floor = max(atr, closes[-1] * 0.0001, 1e-12)

    fast = _ema(closes, config.fast_ema_period)
    slow = _ema(closes, config.slow_ema_period)
    separation_raw = abs(fast - slow) / atr_floor
    separation = _clamp(separation_raw / 1.2)

    lookback = min(config.regime_lookback, count)
    recent_closes = closes[-lookback:]
    path = sum(abs(b - a) for a, b in zip(recent_closes[:-1], recent_closes[1:]))
    net = recent_closes[-1] - recent_closes[0]
    efficiency = _clamp(abs(net) / path) if path > 0 else 0.0

    previous_highs = bars.highs[-(lookback + 1) : -1] or bars.highs[:-1]
    previous_lows = bars.lows[-(lookback + 1) : -1] or bars.lows[:-1]
    upper = max(previous_highs) if previous_highs else closes[-1]
    lower = min(previous_lows) if previous_lows else closes[-1]
    width = max(upper - lower, atr_floor)
    position = _clamp((closes[-1] - lower) / width)
    donchian_edge = _clamp(abs(position - 0.5) * 2.0)

    ema_direction = 1 if fast > slow else -1 if fast < slow else 0
    net_direction = 1 if net > 0 else -1 if net < 0 else 0
    alignment = 1.0 if ema_direction and ema_direction == net_direction else 0.0

    trend = _clamp(
        0.40 * efficiency + 0.35 * separation + 0.15 * donchian_edge + 0.10 * alignment
    )
    range_score = _clamp(
        0.50 * (1.0 - efficiency)
        + 0.30 * (1.0 - separation)
        + 0.20 * (1.0 - donchian_edge)
    )

    recent_tr = true_ranges[-3:]
    baseline_tr = true_ranges[-13:-3] or true_ranges[:-3] or true_ranges
    recent_mean = sum(recent_tr) / len(recent_tr)
    baseline_mean = sum(baseline_tr) / len(baseline_tr) if baseline_tr else atr_floor
    volatility_ratio = recent_mean / max(baseline_mean, 1e-12)
    shock_atr = true_ranges[-1] / atr_floor
    gap_atr = abs(bars.opens[-1] - closes[-2]) / atr_floor
    spread = max(0.0, spread_bps or 0.0)

    vol_danger = _clamp((volatility_ratio - 1.15) / 1.35)
    shock_danger = _clamp((shock_atr - 1.30) / 2.70)
    gap_danger = _clamp((gap_atr - 0.50) / 2.50)
    spread_danger = _clamp((spread - 20.0) / 80.0)
    weighted_danger = (
        0.45 * vol_danger
        + 0.30 * shock_danger
        + 0.15 * gap_danger
        + 0.10 * spread_danger
    )
    danger = _clamp(max(weighted_danger, shock_danger * 0.80))

    if trend >= config.trend_exit and ema_direction > 0:
        bias = WaveBias.BULLISH
    elif trend >= config.trend_exit and ema_direction < 0:
        bias = WaveBias.BEARISH
    else:
        bias = WaveBias.NEUTRAL

    reasons: list[str] = []
    if trend > range_score:
        reasons.append("WAVE_TREND_DOMINANT")
    elif range_score > trend:
        reasons.append("WAVE_RANGE_DOMINANT")
    if volatility_ratio >= 1.50:
        reasons.append("WAVE_VOLATILITY_EXPANSION")
    if shock_atr >= 2.0:
        reasons.append("WAVE_PRICE_SHOCK")
    if donchian_edge >= 0.85:
        reasons.append("WAVE_DONCHIAN_EDGE")

    scores = WaveScores(
        trend=_round(trend),
        range=_round(range_score),
        danger=_round(danger),
        efficiency=_round(efficiency),
        ema_separation_atr=_round(separation_raw),
        donchian_edge=_round(donchian_edge),
        volatility_ratio=_round(volatility_ratio),
        shock_atr=_round(shock_atr),
    )
    return scores, atr, bias, tuple(reasons)


def _candidate_mode(
    scores: WaveScores, data_quality: str, config: WaveConfig
) -> WaveMode:
    if data_quality != "READY":
        return WaveMode.WAIT
    if scores.danger >= config.danger_enter:
        return WaveMode.DANGER_PAUSE
    if (
        scores.trend >= config.trend_enter
        and scores.trend - scores.range >= config.score_margin
    ):
        return WaveMode.TREND_FOLLOW
    if scores.range >= config.range_enter:
        return WaveMode.RANGE_GRID
    return WaveMode.WAIT


def _true_ranges(bars: _NormalizedBars) -> tuple[float, ...]:
    out: list[float] = []
    previous = bars.closes[0]
    for high, low, close in zip(bars.highs, bars.lows, bars.closes):
        out.append(max(high - low, abs(high - previous), abs(low - previous)))
        previous = close
    return tuple(out)


def _ema(values: Sequence[float], period: int) -> float:
    alpha = 2.0 / (max(1, period) + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _observation_id(*, key: str, as_of: str, mode: WaveMode, scores: WaveScores) -> str:
    payload = (
        f"{MODEL_VERSION}|{key}|{as_of}|{mode.value}|"
        f"{scores.trend:.6f}|{scores.range:.6f}|{scores.danger:.6f}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _first_float(bar: Mapping[str, object], *names: str) -> Optional[float]:
    for name in names:
        value = _safe_float(bar.get(name))
        if value is not None and math.isfinite(value):
            return value
    return None


def _safe_float(value: object) -> Optional[float]:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round(value: float) -> float:
    return round(float(value), 6)


def _dedupe_codes(codes: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(code for code in codes if code))
