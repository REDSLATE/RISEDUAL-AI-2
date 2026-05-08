"""Tests for the crypto Strategist / Auditor adversarial signal layer."""
from __future__ import annotations

from services.crypto_strategist import (
    adversarial_signal,
    auditor_review,
    strategist_signal,
    _ema,
    _momentum_pct,
    _rsi,
)


# ── Indicator math ────────────────────────────────────────────────────────────


def test_rsi_returns_none_for_short_series():
    assert _rsi([1.0, 2.0, 3.0]) is None


def test_rsi_full_uptrend_pegs_to_100():
    closes = list(range(1, 30))  # strictly increasing → all gains, no losses
    rsi = _rsi([float(x) for x in closes])
    assert rsi == 100.0


def test_rsi_balanced_oscillation_lands_near_50():
    # Alternating up/down → RSI should be near 50
    closes = [100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0,
              100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0]
    rsi = _rsi(closes)
    assert rsi is not None
    assert 40.0 < rsi < 60.0


def test_ema_seeds_on_first_period():
    closes = [10.0] * 20
    ema = _ema(closes, 20)
    assert ema == 10.0


def test_momentum_pct_returns_none_when_too_short():
    assert _momentum_pct([1.0, 2.0], 5) is None


def test_momentum_pct_basic():
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]  # 5-bar move +5%
    assert _momentum_pct(closes, 5) == 0.05


def _moderate_uptrend(n: int = 60, base: float = 100.0,
                      drift: float = 0.4) -> list[float]:
    """Synthetic uptrend with realistic pullback noise so RSI lands
    in the 55-65 range (not pegged to 100). Pattern: 2 bars up,
    1 bar down with equal drift → ~+33% net upward bias, RSI ~64."""
    series = [base]
    for i in range(1, n):
        if i % 3 == 2:  # every 3rd bar = pullback
            series.append(series[-1] - drift)
        else:
            series.append(series[-1] + drift)
    return series


def _moderate_downtrend(n: int = 60, base: float = 100.0,
                        drift: float = 0.4) -> list[float]:
    series = [base]
    for i in range(1, n):
        if i % 3 == 2:
            series.append(series[-1] + drift)
        else:
            series.append(series[-1] - drift)
    return series


# ── Strategist proposals ──────────────────────────────────────────────────────


def test_strategist_holds_on_short_history():
    sig = strategist_signal([100.0] * 10)
    assert sig["direction"] == "HOLD"
    assert sig["reason"] == "insufficient_history"


def test_strategist_proposes_long_on_clean_uptrend():
    closes = _moderate_uptrend(60)
    sig = strategist_signal(closes)
    assert sig["direction"] == "LONG"
    assert sig["confidence"] >= 0.55


def test_strategist_proposes_short_on_clean_downtrend():
    closes = _moderate_downtrend(60)
    sig = strategist_signal(closes)
    assert sig["direction"] == "SHORT"
    assert sig["confidence"] >= 0.55


def test_strategist_holds_on_flat_bars():
    sig = strategist_signal([100.0] * 60)
    assert sig["direction"] == "HOLD"


# ── Auditor vetoes ────────────────────────────────────────────────────────────


def test_auditor_vetoes_overbought_long():
    """RSI ≥ 70 on a proposed LONG should veto."""
    # Construct a series that's strongly trending up — RSI will hit 100
    closes = [100.0 + i * 2.0 for i in range(60)]
    proposal = {"direction": "LONG", "confidence": 0.8, "reason": "trend"}
    audit = auditor_review(closes, proposal)
    assert audit["verdict"] == "VETO"
    assert "rsi_overbought" in audit["reason"]


def test_auditor_vetoes_oversold_short():
    # Higher base so the linear -2/bar series stays above zero for
    # all 60 bars; RSI/momentum stay defined.
    closes = [200.0 - i * 2.0 for i in range(60)]
    proposal = {"direction": "SHORT", "confidence": 0.8, "reason": "trend"}
    audit = auditor_review(closes, proposal)
    assert audit["verdict"] == "VETO"
    # Oversold RSI or capitulation momentum — both are valid vetoes
    # for a SHORT in a freefall (different exhaustion patterns).
    assert ("rsi_oversold" in audit["reason"]
            or "capitulation_short_momentum" in audit["reason"])


def test_auditor_confirms_moderate_long():
    closes = _moderate_uptrend(60)
    proposal = {"direction": "LONG", "confidence": 0.7, "reason": "trend"}
    audit = auditor_review(closes, proposal)
    assert audit["verdict"] == "CONFIRM"
    assert audit["confidence"] > 0.5


def test_auditor_returns_hold_on_hold_proposal():
    audit = auditor_review([100.0] * 60, {"direction": "HOLD"})
    assert audit["verdict"] == "HOLD"


# ── Combined adversarial output ───────────────────────────────────────────────


def test_adversarial_long_fires_on_moderate_uptrend():
    closes = _moderate_uptrend(60)
    sig = adversarial_signal(closes)
    assert sig["direction"] == "LONG"
    assert sig["confidence"] >= 0.55
    # Both blocks present for explainability
    assert sig["strategist"]["direction"] == "LONG"
    assert sig["auditor"]["verdict"] == "CONFIRM"


def test_adversarial_blocks_overbought_long():
    """Strong uptrend → Strategist proposes LONG, Auditor vetoes (RSI ≥ 70)."""
    closes = [100.0 + i * 2.0 for i in range(60)]
    sig = adversarial_signal(closes)
    assert sig["direction"] == "HOLD"
    assert sig["reason"].startswith("auditor_veto")
    assert sig["strategist"]["direction"] == "LONG"
    assert sig["auditor"]["verdict"] == "VETO"


def test_adversarial_holds_on_flat():
    sig = adversarial_signal([100.0] * 60)
    assert sig["direction"] == "HOLD"
    assert sig["reason"].startswith("strategist_hold")
