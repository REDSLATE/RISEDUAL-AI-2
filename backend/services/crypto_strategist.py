"""Crypto Strategist / Auditor — adversarial signal layer.

Mirrors the equity War Room's "Strategist proposes, Auditor vetoes"
structure but runs entirely on deterministic technical-analysis
math (RSI, EMA crossover, momentum, Bollinger position). No LLM
calls, no external crew — every output is reproducible from the
input OHLC bars.

Why deterministic instead of CrewAI?
------------------------------------
1. **Cost.** The crypto bot ticks every 15 minutes across 3+ symbols;
   running an LLM crew per symbol per tick burns Universal Key
   credits without a clear edge over rule-based TA at the
   minute-bar timescale.
2. **Speed.** A scheduler tick must finish in under a second so the
   APScheduler doesn't fall behind. Deterministic TA returns in
   milliseconds; CrewAI averages 15-25s per symbol.
3. **Testability.** Every Strategist/Auditor decision can be
   unit-tested with synthetic OHLC bars — no API mocks needed.

The adversarial structure
-------------------------
* :func:`strategist_signal` proposes (direction, confidence, reason).
* :func:`auditor_review` independently inspects the SAME bars and
  either confirms or vetoes with its own reason.
* :func:`adversarial_signal` combines both: trade fires only when
  both agree on the direction AND combined confidence ≥ floor.

The Strategist tilts toward momentum (early entries on trend
formation); the Auditor tilts toward mean-reversion exhaustion
checks (vetoes when RSI is dangerously stretched the wrong way).
That's the same "asymmetric perspective → adversarial check"
pattern the equity War Room uses — just compressed into pure math.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────
# Kept inline so the crypto signal layer is self-contained and any
# future divergence from equity is a deliberate code change, not a
# config-coupling accident.

_RSI_OVERBOUGHT = 70.0
_RSI_OVERSOLD = 30.0
_RSI_HOT = 65.0          # weaker than overbought — Auditor vetos LONGs above
_RSI_COLD = 35.0         # weaker than oversold — Auditor vetos SHORTs below
_MIN_BARS = 30           # need at least 30 daily/hourly bars for stable signals
_MOMENTUM_LOOKBACK = 5   # bars for short-term momentum check
_MIN_CONFIDENCE = 0.60   # adversarial floor — below this, no trade


def _rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI — pure-python, no numpy dependency. Returns None
    when insufficient data."""
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(closes: list[float], period: int) -> Optional[float]:
    """Standard EMA. Seeded with the simple mean of the first
    ``period`` values."""
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for px in closes[period:]:
        ema = px * k + ema * (1.0 - k)
    return ema


def _momentum_pct(closes: list[float], lookback: int) -> Optional[float]:
    """Percent change over the last ``lookback`` bars, expressed as a
    decimal (0.04 = +4%)."""
    if len(closes) <= lookback:
        return None
    past = closes[-lookback - 1]
    now = closes[-1]
    if past <= 0:
        return None
    return (now - past) / past


# ── Strategist ────────────────────────────────────────────────────────────────


def strategist_signal(closes: list[float]) -> dict[str, Any]:
    """Trend / momentum proposer.

    Logic
    -----
    * **LONG** when price > EMA20 AND RSI > 50 AND 5-bar momentum > 0.
    * **SHORT** when price < EMA20 AND RSI < 50 AND 5-bar momentum < 0.
    * Else **HOLD**.

    Confidence reflects how cleanly all three components align.
    """
    if not closes or len(closes) < _MIN_BARS:
        return {
            "direction": "HOLD",
            "confidence": 0.0,
            "reason": "insufficient_history",
        }

    rsi = _rsi(closes, 14)
    ema20 = _ema(closes, 20)
    mom = _momentum_pct(closes, _MOMENTUM_LOOKBACK)
    last = closes[-1]

    if rsi is None or ema20 is None or mom is None:
        return {
            "direction": "HOLD",
            "confidence": 0.0,
            "reason": "indicators_unavailable",
        }

    above_ema = last > ema20
    rsi_bullish = rsi > 50
    mom_up = mom > 0

    # All three agree → LONG with strength
    if above_ema and rsi_bullish and mom_up:
        # Confidence climbs as RSI distance from 50 grows + momentum pct
        conf = min(0.95, 0.55 + (rsi - 50) / 100.0 + min(abs(mom), 0.05) * 4.0)
        return {
            "direction": "LONG",
            "confidence": round(conf, 3),
            "reason": (
                f"price_above_ema20={above_ema} rsi={rsi:.1f} "
                f"momentum_5b={mom * 100:.2f}%"
            ),
            "indicators": {"rsi": rsi, "ema20": ema20, "momentum_5b": mom},
        }

    if (not above_ema) and (not rsi_bullish) and (not mom_up):
        conf = min(0.95, 0.55 + (50 - rsi) / 100.0 + min(abs(mom), 0.05) * 4.0)
        return {
            "direction": "SHORT",
            "confidence": round(conf, 3),
            "reason": (
                f"price_below_ema20={not above_ema} rsi={rsi:.1f} "
                f"momentum_5b={mom * 100:.2f}%"
            ),
            "indicators": {"rsi": rsi, "ema20": ema20, "momentum_5b": mom},
        }

    return {
        "direction": "HOLD",
        "confidence": 0.0,
        "reason": (
            f"signals_split price_above_ema20={above_ema} rsi={rsi:.1f} "
            f"momentum={mom * 100:.2f}%"
        ),
        "indicators": {"rsi": rsi, "ema20": ema20, "momentum_5b": mom},
    }


# ── Auditor ───────────────────────────────────────────────────────────────────


def auditor_review(closes: list[float], proposal: dict) -> dict[str, Any]:
    """Independent veto check on the Strategist's proposal.

    The Auditor isn't trying to find an opposing signal — it's
    checking for setups that are already too stretched in the
    proposed direction (mean-reversion risk).

    Vetoes
    ------
    * Proposed **LONG** but RSI ≥ 70 (overbought, bounce risk high).
    * Proposed **SHORT** but RSI ≤ 30 (oversold, bounce risk high).
    * Proposed **LONG** but 5-bar momentum > 8% (parabolic, snap-back risk).
    * Proposed **SHORT** but 5-bar momentum < −8% (capitulation, bounce risk).

    Otherwise the Auditor confirms with its own confidence score
    (decays as proposal nears hot/cold territory)."""
    direction = proposal.get("direction", "HOLD")
    if direction == "HOLD":
        return {"verdict": "HOLD", "confidence": 0.0, "reason": "no_proposal"}

    if not closes or len(closes) < _MIN_BARS:
        return {
            "verdict": "VETO",
            "confidence": 0.0,
            "reason": "auditor_insufficient_history",
        }

    rsi = _rsi(closes, 14)
    mom = _momentum_pct(closes, _MOMENTUM_LOOKBACK)
    if rsi is None or mom is None:
        return {
            "verdict": "VETO",
            "confidence": 0.0,
            "reason": "auditor_indicators_unavailable",
        }

    # Stretched-RSI veto
    if direction == "LONG" and rsi >= _RSI_OVERBOUGHT:
        return {
            "verdict": "VETO",
            "confidence": 0.0,
            "reason": f"rsi_overbought_{rsi:.1f}",
            "indicators": {"rsi": rsi, "momentum_5b": mom},
        }
    if direction == "SHORT" and rsi <= _RSI_OVERSOLD:
        return {
            "verdict": "VETO",
            "confidence": 0.0,
            "reason": f"rsi_oversold_{rsi:.1f}",
            "indicators": {"rsi": rsi, "momentum_5b": mom},
        }

    # Parabolic-move veto (any single 5-bar move ≥8% is exhaustion territory)
    if direction == "LONG" and mom >= 0.08:
        return {
            "verdict": "VETO",
            "confidence": 0.0,
            "reason": f"parabolic_long_momentum_{mom * 100:.2f}%",
            "indicators": {"rsi": rsi, "momentum_5b": mom},
        }
    if direction == "SHORT" and mom <= -0.08:
        return {
            "verdict": "VETO",
            "confidence": 0.0,
            "reason": f"capitulation_short_momentum_{mom * 100:.2f}%",
            "indicators": {"rsi": rsi, "momentum_5b": mom},
        }

    # Confirm — confidence decays as RSI nears veto territory
    if direction == "LONG":
        # Closer to 70 = lower auditor confidence
        head_room = max(0.0, _RSI_OVERBOUGHT - rsi) / 20.0  # 0 at 70, 1 at 50
    else:
        head_room = max(0.0, rsi - _RSI_OVERSOLD) / 20.0

    audit_conf = round(min(0.95, 0.55 + head_room * 0.35), 3)
    return {
        "verdict": "CONFIRM",
        "confidence": audit_conf,
        "reason": f"head_room_ok rsi={rsi:.1f} momentum_5b={mom * 100:.2f}%",
        "indicators": {"rsi": rsi, "momentum_5b": mom},
    }


# ── Combined adversarial signal ───────────────────────────────────────────────


def adversarial_signal(closes: list[float]) -> dict[str, Any]:
    """Run Strategist → Auditor → combine.

    Returns
    -------
    dict
        ``{direction, confidence, reason, strategist, auditor}``
        where:
          * ``direction`` ∈ {"LONG", "SHORT", "HOLD"}.
          * ``confidence`` is the geometric mean of strategist + auditor
            confidence (so a weak link drags the trade down).
          * ``reason`` summarises the joint verdict.
          * ``strategist`` and ``auditor`` carry the individual blocks
            for audit/explainability.

        ``HOLD`` fires when:
          * Strategist returned HOLD, OR
          * Auditor vetoed, OR
          * Combined confidence < ``_MIN_CONFIDENCE``.
    """
    proposal = strategist_signal(closes)
    audit = auditor_review(closes, proposal)

    if proposal["direction"] == "HOLD":
        return {
            "direction": "HOLD",
            "confidence": 0.0,
            "reason": f"strategist_hold:{proposal.get('reason')}",
            "strategist": proposal,
            "auditor": audit,
        }

    if audit["verdict"] == "VETO":
        return {
            "direction": "HOLD",
            "confidence": 0.0,
            "reason": f"auditor_veto:{audit.get('reason')}",
            "strategist": proposal,
            "auditor": audit,
        }

    # Geometric mean — penalises asymmetric confidence harder than arithmetic
    combined = round(
        (float(proposal["confidence"]) * float(audit["confidence"])) ** 0.5,
        3,
    )

    if combined < _MIN_CONFIDENCE:
        return {
            "direction": "HOLD",
            "confidence": combined,
            "reason": f"combined_below_floor_{combined:.3f}<{_MIN_CONFIDENCE}",
            "strategist": proposal,
            "auditor": audit,
        }

    return {
        "direction": proposal["direction"],
        "confidence": combined,
        "reason": (
            f"strategist:{proposal.get('reason')} | "
            f"auditor:{audit.get('reason')}"
        ),
        "strategist": proposal,
        "auditor": audit,
    }
