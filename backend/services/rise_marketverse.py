"""RISE MarketVerse — world-state inference + shadow brain arena.

v0.1 — SHADOW ONLY.
-------------------
Per operator directive 2026-06-01: this module lives in Alpha's
research / shadow layer, NOT the live decision path. The full doctrine:

  Market data
      ↓
  rise_marketverse.MarketWorldModel.infer_state(bars)
      ↓
  MarketState
      ↓
  rise_marketverse.ShadowBrainArena.decide_all(state)
      ↓
  marketverse_observations   ← Mongo collection (NEW, isolated)

  ❌ NOT touching ❌
      live decision loop
      broker authority
      paper-trade collections
      research_shadow_decisions (existing scorer collection)

Phase progression
-----------------
* **Phase 0 (this module — v0.1)**: shadow-only observations land
  in ``marketverse_observations``. No live consumption.
* **Phase 1 (later)**: ``MarketState`` becomes an OPTIONAL feature
  input to ``strategist.evaluate(...)`` — informational only,
  never authority.
* **Phase 2 (after 30-60 days of shadow evidence)**: promotion
  candidate via the existing research-shadow promotion gates.
* **Phase 3 (earned, not granted)**: direct broker authority — never
  before the promotion framework signs off.

Design rules pinned
-------------------
* **Deterministic.** No LLM, no network, no clock-dependent math
  except for the observation timestamp. Same inputs → same output.
* **Pure functions where possible.** ``MarketWorldModel`` and
  ``ShadowBrainArena`` hold no mutable state; both are factory-style
  so tests can swap parameters without monkeypatching.
* **Inputs come in as plain dicts / lists.** No Mongo, no broker
  imports — keeps the module zero-dependency and snappy in tests.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional


# ── Data shapes ────────────────────────────────────────────────────────


@dataclass
class ProbablePath:
    """One scenario in the world-model's path projection."""
    name: str          # "bull" | "base" | "bear"
    price: float       # projected price at horizon
    prob: float        # 0.0 - 1.0
    horizon_bars: int  # how many bars ahead


@dataclass
class MarketState:
    """Snapshot the world model infers from a bar window.

    All fields are JSON-serialisable so the state can be persisted
    via ``asdict(state)`` straight into Mongo.
    """
    symbol: str
    regime: str
    volatility: float
    volatility_band: str
    momentum_5b: float
    rsi_14: float
    last_close: float
    bar_count: int
    probable_paths: list[ProbablePath] = field(default_factory=list)
    timestamp: str = ""
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainOpinion:
    """One brain's read of a MarketState."""
    brain_name: str
    action: str       # "LONG" | "SHORT" | "HOLD"
    confidence: float
    reasoning: str


# ── Indicator math (pure) ──────────────────────────────────────────────


def _closes_from_bars(bars: Iterable[Mapping[str, Any]]) -> list[float]:
    """Pull the ``c`` (close) field from each bar. Skips any bar
    missing or non-numeric — caller gets a clean float list."""
    out: list[float] = []
    for b in bars:
        c = b.get("c")
        if isinstance(c, (int, float)) and math.isfinite(c):
            out.append(float(c))
    return out


def _rsi_14(closes: list[float]) -> float:
    """Classic 14-period RSI. Returns 50.0 when insufficient data
    (the neutral midpoint — caller's regime classifier will read
    this as "no RSI signal")."""
    if len(closes) < 15:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, 15):
        diff = closes[-15 + i] - closes[-16 + i]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
    avg_gain = sum(gains) / 14.0
    avg_loss = sum(losses) / 14.0
    # Both sides zero → perfectly flat window. Default to 50.0
    # (neutral) rather than 100.0, which would otherwise leak from
    # the ``avg_loss == 0`` branch below and trip the regime
    # classifier into "overbought" on a pancake-flat market.
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _momentum_5b(closes: list[float]) -> float:
    """5-bar % change. Signed."""
    if len(closes) < 6:
        return 0.0
    older = closes[-6]
    latest = closes[-1]
    if older <= 0:
        return 0.0
    return (latest - older) / older


def _realised_vol(closes: list[float], lookback: int = 20) -> float:
    """Sample stdev of log returns over the lookback window.
    Returned as a per-bar fraction (e.g. 0.015 = 1.5% per bar)."""
    if len(closes) < lookback + 1:
        lookback = max(2, len(closes) - 1)
    if lookback < 2:
        return 0.0
    rets: list[float] = []
    for i in range(-lookback, 0):
        prev = closes[i - 1]
        cur = closes[i]
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 2:
        return 0.0
    try:
        return statistics.stdev(rets)
    except statistics.StatisticsError:
        return 0.0


def _classify_volatility_band(per_bar_vol: float) -> str:
    """Bin per-bar vol into 4 ordered bands. Thresholds tuned for
    crypto bar timescales (1h/4h); equity bars trend a hair lower
    but the bands still separate sensibly."""
    if per_bar_vol < 0.005:
        return "low"
    if per_bar_vol < 0.015:
        return "normal"
    if per_bar_vol < 0.035:
        return "elevated"
    return "extreme"


def _classify_regime(rsi: float, momentum: float, vol_band: str) -> str:
    """Synthesise rsi + momentum + vol into one regime label.

    Labels (v0.1 — intentionally coarse):
      * ``parabolic_up``   — strong upside burst (high vol + bullish mom + RSI > 70)
      * ``parabolic_down`` — strong downside burst (high vol + bearish mom + RSI < 30)
      * ``trending_up``    — sustained upside (positive mom + RSI 50-70)
      * ``trending_down``  — sustained downside (negative mom + RSI 30-50)
      * ``overbought``     — exhausted upside (RSI > 70, low/normal vol)
      * ``oversold``       — exhausted downside (RSI < 30, low/normal vol)
      * ``range``          — RSI 40-60, very low vol
      * ``uncertain``      — anything else
    """
    if vol_band in ("elevated", "extreme") and momentum > 0.005 and rsi > 70:
        return "parabolic_up"
    if vol_band in ("elevated", "extreme") and momentum < -0.005 and rsi < 30:
        return "parabolic_down"
    if rsi > 70:
        return "overbought"
    if rsi < 30:
        return "oversold"
    if momentum > 0.003 and rsi > 50:
        return "trending_up"
    if momentum < -0.003 and rsi < 50:
        return "trending_down"
    if 40 <= rsi <= 60 and vol_band == "low":
        return "range"
    return "uncertain"


# Per-regime path-probability weights. Sum to 1.0 per row.
_REGIME_PATH_PROBS: dict[str, tuple[float, float, float]] = {
    # regime → (bull_prob, base_prob, bear_prob)
    "parabolic_up":   (0.55, 0.20, 0.25),   # mean-reversion risk
    "parabolic_down": (0.25, 0.20, 0.55),
    "trending_up":    (0.55, 0.30, 0.15),
    "trending_down":  (0.15, 0.30, 0.55),
    "overbought":     (0.20, 0.30, 0.50),
    "oversold":       (0.50, 0.30, 0.20),
    "range":          (0.30, 0.40, 0.30),
    "uncertain":      (0.33, 0.34, 0.33),
}


def _build_paths(
    last_close: float, per_bar_vol: float, regime: str,
    horizon_bars: int = 10,
) -> list[ProbablePath]:
    """Three-scenario path projection. Pure deterministic — no
    Monte Carlo. Bull/bear levels are ±2σ at the horizon, scaled
    by the per-bar volatility.

    Probabilities come from ``_REGIME_PATH_PROBS`` and never sum
    to anything other than 1.0 (within float epsilon).
    """
    if last_close <= 0:
        return []
    horizon_sigma = per_bar_vol * math.sqrt(max(horizon_bars, 1))
    bull = last_close * (1.0 + 2.0 * horizon_sigma)
    bear = last_close * (1.0 - 2.0 * horizon_sigma)
    probs = _REGIME_PATH_PROBS.get(regime, _REGIME_PATH_PROBS["uncertain"])
    return [
        ProbablePath(name="bull", price=round(bull, 6),
                     prob=probs[0], horizon_bars=horizon_bars),
        ProbablePath(name="base", price=round(last_close, 6),
                     prob=probs[1], horizon_bars=horizon_bars),
        ProbablePath(name="bear", price=round(bear, 6),
                     prob=probs[2], horizon_bars=horizon_bars),
    ]


# ── MarketWorldModel ───────────────────────────────────────────────────


class MarketWorldModel:
    """Stateless world-state inferer. Takes a list of OHLCV bars,
    returns a :class:`MarketState`.

    Pure-function in spirit — instances hold no learned weights,
    no caches, no I/O. The class shape is preserved for v0.2 when
    we'll want a per-symbol model with calibrated thresholds.
    """

    def __init__(self, horizon_bars: int = 10) -> None:
        self.horizon_bars = max(1, int(horizon_bars))

    def infer_state(
        self, bars: list[Mapping[str, Any]], *, symbol: str = "",
    ) -> MarketState:
        """Compute a :class:`MarketState` from ``bars``.

        Returns a populated state even for short bar lists — the
        scorer will mark sparse rows with ``regime="uncertain"``
        and ``volatility_band="low"`` rather than refusing to emit
        an observation. The downstream scorer can filter by
        ``bar_count`` if it wants a maturity floor.
        """
        closes = _closes_from_bars(bars)
        bar_count = len(closes)
        if bar_count == 0:
            return MarketState(
                symbol=symbol,
                regime="uncertain",
                volatility=0.0,
                volatility_band="low",
                momentum_5b=0.0,
                rsi_14=50.0,
                last_close=0.0,
                bar_count=0,
                probable_paths=[],
                timestamp=datetime.now(timezone.utc).isoformat(),
                notes={"empty_bars": True},
            )
        last_close = closes[-1]
        rsi = _rsi_14(closes)
        momentum = _momentum_5b(closes)
        vol = _realised_vol(closes)
        vol_band = _classify_volatility_band(vol)
        regime = _classify_regime(rsi, momentum, vol_band)
        paths = _build_paths(last_close, vol, regime, self.horizon_bars)
        return MarketState(
            symbol=symbol,
            regime=regime,
            volatility=round(vol, 6),
            volatility_band=vol_band,
            momentum_5b=round(momentum, 6),
            rsi_14=round(rsi, 4),
            last_close=round(last_close, 6),
            bar_count=bar_count,
            probable_paths=paths,
            timestamp=datetime.now(timezone.utc).isoformat(),
            notes={},
        )


# ── ShadowBrainArena (the disagreement experiment surface) ─────────────


def _brain_momentum(state: MarketState) -> BrainOpinion:
    """Follows momentum. Long when 5b mom > +0.3%, short when
    < -0.3%, hold otherwise. Confidence scales with |momentum|."""
    if state.momentum_5b > 0.003:
        return BrainOpinion(
            brain_name="momentum",
            action="LONG",
            confidence=min(1.0, abs(state.momentum_5b) * 50.0),
            reasoning=f"5b_momentum={state.momentum_5b*100:+.2f}%",
        )
    if state.momentum_5b < -0.003:
        return BrainOpinion(
            brain_name="momentum", action="SHORT",
            confidence=min(1.0, abs(state.momentum_5b) * 50.0),
            reasoning=f"5b_momentum={state.momentum_5b*100:+.2f}%",
        )
    return BrainOpinion(
        brain_name="momentum", action="HOLD", confidence=0.0,
        reasoning=f"flat 5b_momentum={state.momentum_5b*100:+.2f}%",
    )


def _brain_mean_revert(state: MarketState) -> BrainOpinion:
    """Fades RSI extremes. Long below 30, short above 70."""
    if state.rsi_14 < 30:
        return BrainOpinion(
            brain_name="mean_revert", action="LONG",
            confidence=(30.0 - state.rsi_14) / 30.0,
            reasoning=f"rsi_oversold={state.rsi_14:.1f}",
        )
    if state.rsi_14 > 70:
        return BrainOpinion(
            brain_name="mean_revert", action="SHORT",
            confidence=(state.rsi_14 - 70.0) / 30.0,
            reasoning=f"rsi_overbought={state.rsi_14:.1f}",
        )
    return BrainOpinion(
        brain_name="mean_revert", action="HOLD", confidence=0.0,
        reasoning=f"rsi_neutral={state.rsi_14:.1f}",
    )


def _brain_volatility(state: MarketState) -> BrainOpinion:
    """Sits out in extreme vol, otherwise trusts the regime."""
    if state.volatility_band == "extreme":
        return BrainOpinion(
            brain_name="volatility", action="HOLD", confidence=0.8,
            reasoning=f"extreme_vol={state.volatility:.4f}",
        )
    if state.volatility_band == "elevated":
        return BrainOpinion(
            brain_name="volatility", action="HOLD", confidence=0.5,
            reasoning=f"elevated_vol={state.volatility:.4f}",
        )
    if state.regime in ("trending_up", "parabolic_up"):
        return BrainOpinion(
            brain_name="volatility", action="LONG", confidence=0.5,
            reasoning=f"calm_regime={state.regime}",
        )
    if state.regime in ("trending_down", "parabolic_down"):
        return BrainOpinion(
            brain_name="volatility", action="SHORT", confidence=0.5,
            reasoning=f"calm_regime={state.regime}",
        )
    return BrainOpinion(
        brain_name="volatility", action="HOLD", confidence=0.3,
        reasoning=f"calm_neutral={state.regime}",
    )


def _brain_regime(state: MarketState) -> BrainOpinion:
    """Aligns with the regime label, conviction tied to vol band."""
    if state.regime in ("trending_up", "parabolic_up"):
        return BrainOpinion(
            brain_name="regime", action="LONG", confidence=0.7,
            reasoning=f"regime={state.regime}",
        )
    if state.regime in ("trending_down", "parabolic_down"):
        return BrainOpinion(
            brain_name="regime", action="SHORT", confidence=0.7,
            reasoning=f"regime={state.regime}",
        )
    if state.regime == "overbought":
        return BrainOpinion(
            brain_name="regime", action="SHORT", confidence=0.55,
            reasoning=f"regime={state.regime}",
        )
    if state.regime == "oversold":
        return BrainOpinion(
            brain_name="regime", action="LONG", confidence=0.55,
            reasoning=f"regime={state.regime}",
        )
    return BrainOpinion(
        brain_name="regime", action="HOLD", confidence=0.0,
        reasoning=f"regime={state.regime}",
    )


def _brain_path_weighted(state: MarketState) -> BrainOpinion:
    """Reads the probable_paths and tilts toward the higher-prob
    direction. Used as the synthesised "world-model" voice."""
    if not state.probable_paths:
        return BrainOpinion(
            brain_name="path_weighted", action="HOLD", confidence=0.0,
            reasoning="no_paths",
        )
    bull = next((p for p in state.probable_paths if p.name == "bull"), None)
    bear = next((p for p in state.probable_paths if p.name == "bear"), None)
    if bull is None or bear is None:
        return BrainOpinion(
            brain_name="path_weighted", action="HOLD", confidence=0.0,
            reasoning="paths_missing_legs",
        )
    if bull.prob - bear.prob > 0.10:
        return BrainOpinion(
            brain_name="path_weighted", action="LONG",
            confidence=min(1.0, bull.prob),
            reasoning=f"bull_prob={bull.prob:.2f}>bear_prob={bear.prob:.2f}",
        )
    if bear.prob - bull.prob > 0.10:
        return BrainOpinion(
            brain_name="path_weighted", action="SHORT",
            confidence=min(1.0, bear.prob),
            reasoning=f"bear_prob={bear.prob:.2f}>bull_prob={bull.prob:.2f}",
        )
    return BrainOpinion(
        brain_name="path_weighted", action="HOLD",
        confidence=0.0,
        reasoning=f"paths_balanced bull={bull.prob:.2f} bear={bear.prob:.2f}",
    )


# Default arena roster. Operator can pass a custom roster via the
# constructor if they want to A/B different brain sets.
DEFAULT_BRAIN_ROSTER = (
    _brain_momentum,
    _brain_mean_revert,
    _brain_volatility,
    _brain_regime,
    _brain_path_weighted,
)


class ShadowBrainArena:
    """Runs every brain in its roster against the same
    :class:`MarketState` and returns the panel of opinions —
    nothing more.

    The arena does NOT pick a winner. Disagreement is the value
    we're capturing; consensus comes later, in a separate
    scorer/promotion module.
    """

    def __init__(self, brains: tuple = DEFAULT_BRAIN_ROSTER) -> None:
        if not brains:
            raise ValueError("ShadowBrainArena: brains roster must be non-empty")
        self.brains = brains

    def decide_all(self, state: MarketState) -> list[BrainOpinion]:
        out: list[BrainOpinion] = []
        for fn in self.brains:
            try:
                opinion = fn(state)
            except Exception as exc:  # noqa: BLE001
                opinion = BrainOpinion(
                    brain_name=getattr(fn, "__name__", "anon"),
                    action="HOLD", confidence=0.0,
                    reasoning=f"error: {type(exc).__name__}",
                )
            out.append(opinion)
        return out


# ── Disagreement metric (for the scorer / observation writer) ──────────


def compute_disagreement(opinions: list[BrainOpinion]) -> dict[str, Any]:
    """Summarise the panel's spread. Pure function — no I/O.

    Returns ``{vote_counts, top_action, disagreement_score}``
    where ``disagreement_score`` is in [0, 1]:
      * 0.0 → all brains agree
      * 1.0 → perfectly split between LONG/SHORT/HOLD
    """
    if not opinions:
        return {"vote_counts": {}, "top_action": "HOLD",
                "disagreement_score": 0.0}
    counts = {"LONG": 0, "SHORT": 0, "HOLD": 0}
    for o in opinions:
        counts[o.action] = counts.get(o.action, 0) + 1
    n = sum(counts.values())
    # Disagreement = 1 - max_share. Single-action = 0 disagreement;
    # 3-way split = ~0.67 disagreement.
    if n == 0:
        return {"vote_counts": counts, "top_action": "HOLD",
                "disagreement_score": 0.0}
    max_share = max(counts.values()) / n
    top_action = max(counts, key=lambda k: counts[k])
    return {
        "vote_counts": counts,
        "top_action": top_action,
        "disagreement_score": round(1.0 - max_share, 4),
    }


def marketstate_asdict(state: MarketState) -> dict[str, Any]:
    """``asdict(state)`` with the nested ProbablePath list also
    converted (asdict does this recursively in py3.10+ but we wrap
    for clarity at call sites).
    """
    return asdict(state)


def opinions_asdict(opinions: list[BrainOpinion]) -> list[dict[str, Any]]:
    return [asdict(o) for o in opinions]


__all__ = [
    "ProbablePath",
    "MarketState",
    "BrainOpinion",
    "MarketWorldModel",
    "ShadowBrainArena",
    "DEFAULT_BRAIN_ROSTER",
    "compute_disagreement",
    "marketstate_asdict",
    "opinions_asdict",
]
