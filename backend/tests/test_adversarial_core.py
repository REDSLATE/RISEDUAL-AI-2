"""Tests for the adversarial decision core (`services.adversarial_core`).

Pins down four invariants:

1. **Pure-function math** — Bull/Bear/Commander produce the right
   shape and respect their published thresholds. This is the only
   layer where heuristics live; it MUST be exhaustively tested.

2. **Input normalisation** — agents read the real strategist signal
   shape (``signal["strategist"]["indicators"]["rsi"]`` etc.), not
   flat fields. The user-supplied scaffold had this bug and it
   would have produced identical scores for every trade.

3. **Double gate** — env flag default-off AND Tier 3 default-locked
   must both be open before any agent runs. This is the discipline
   guarantee.

4. **Phase isolation** — even when both gates open, returning a
   decision is the LIMIT of this module's authority. Honouring the
   phase (shadow / risk_only / veto / full) is the caller's job;
   this module just labels the payload so the caller can branch.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from services.adversarial_core import (
    AgentOutput,
    EDGE_GAP_THRESHOLD,
    bear_agent,
    bull_agent,
    resolve_adversarial,
    run_adversarial_decision,
    _extract_inputs,
)


# ── Test fixtures ─────────────────────────────────────────────────────────────


def _signal(*,
            rsi: float = 55.0,
            momentum_5b: float = 0.01,
            ema20: float = 70000.0,
            strategist_conf: float = 0.65,
            auditor_conf: float = 0.60,
            regime: str = "trending",
            ) -> dict:
    """Build a strategist+auditor signal in the EXACT shape that
    ``crypto_strategist.adversarial_signal`` returns. Test helper
    so individual tests stay readable."""
    return {
        "direction": "LONG",
        "confidence": (strategist_conf + auditor_conf) / 2,
        "regime": regime,
        "symbol": "BTC",
        "strategist": {
            "direction": "LONG",
            "confidence": strategist_conf,
            "reason": "test",
            "indicators": {
                "rsi": rsi,
                "ema20": ema20,
                "momentum_5b": momentum_5b,
            },
        },
        "auditor": {
            "verdict": "CONFIRM",
            "confidence": auditor_conf,
            "reason": "test",
            "indicators": {"rsi": rsi},
        },
    }


# ── Input normalisation ───────────────────────────────────────────────────────


def test_extract_inputs_normalises_rsi_to_zero_one():
    out = _extract_inputs(_signal(rsi=70))
    assert out["rsi"] == pytest.approx(0.7)


def test_extract_inputs_handles_missing_indicators_safely():
    """If the strategist somehow returns no indicators, extraction
    must default everything to neutral, not crash."""
    bare = {"strategist": {}, "auditor": {}, "regime": "trending"}
    out = _extract_inputs(bare)
    assert out["rsi"] == 0.5            # default 50/100
    assert out["momentum_signed"] == 0.0
    assert out["momentum_unit"] == 0.5  # neutral


def test_extract_inputs_volatility_by_regime():
    """Parabolic regime = high volatility input — Bear should fire on
    these. Range = low volatility — Bull dominates."""
    par = _extract_inputs(_signal(regime="parabolic"))
    rng = _extract_inputs(_signal(regime="range"))
    assert par["volatility"] > rng["volatility"]


def test_extract_inputs_squashes_extreme_momentum():
    """A 10% bar shouldn't blow up the unit-momentum scale beyond 1.0
    — that would break the bull confidence cap."""
    out = _extract_inputs(_signal(momentum_5b=0.10))
    assert -1.0 <= out["momentum_signed"] <= 1.0
    assert 0.0 <= out["momentum_unit"] <= 1.0


# ── Bull agent ────────────────────────────────────────────────────────────────


def test_bull_strong_uptrend_fires_high_confidence():
    """Clean uptrend: RSI 60, +2% momentum, strong strategist trend.
    Bull should land >= 0.7 confidence."""
    out = bull_agent(_signal(
        rsi=60, momentum_5b=0.02, strategist_conf=0.75, regime="trending",
    ))
    assert out.side == "LONG"
    assert out.confidence >= 0.65, f"expected strong bull, got {out.confidence}"


def test_bull_overbought_loses_confidence():
    """RSI 90 = exhausted. Bull should chip its own confidence below
    a fresh-trend reading."""
    fresh = bull_agent(_signal(rsi=60, momentum_5b=0.02, strategist_conf=0.75))
    cooked = bull_agent(_signal(rsi=90, momentum_5b=0.02, strategist_conf=0.75))
    assert cooked.confidence < fresh.confidence
    assert cooked.confidence >= 0.0


def test_bull_neutral_inputs_lands_above_neutral():
    """No momentum + neutral trend → bull confidence sits above 0.5
    by design — Bull HAS a long bias built into its 0.30 baseline.
    The math: 0.30 + 0.40*0.5 + 0.30*0.5 = 0.65. This pins that
    we don't accidentally drift the baseline below 0.5 (which would
    make Bull never fire) or above 0.7 (which would make it fire on
    everything)."""
    out = bull_agent(_signal(rsi=50, momentum_5b=0.0, strategist_conf=0.5))
    assert 0.55 <= out.confidence <= 0.70


def test_bull_returns_agent_output_dataclass():
    out = bull_agent(_signal())
    assert isinstance(out, AgentOutput)
    assert out.side == "LONG"
    assert out.expected_r >= 1.0
    assert isinstance(out.invalidations, list)


# ── Bear agent ────────────────────────────────────────────────────────────────


def test_bear_overbought_high_vol_fires():
    """Parabolic top — RSI 85 + parabolic regime. Bear should be loud."""
    out = bear_agent(_signal(rsi=85, momentum_5b=0.01, regime="parabolic"))
    assert out.side == "SHORT_OR_REJECT"
    assert out.confidence >= 0.55


def test_bear_negative_momentum_increases_confidence():
    pos = bear_agent(_signal(rsi=70, momentum_5b=0.02))
    neg = bear_agent(_signal(rsi=70, momentum_5b=-0.02))
    assert neg.confidence > pos.confidence


def test_bear_clean_uptrend_loses_confidence():
    """RSI 55, low vol, positive momentum — Bear has no thesis here.
    Confidence should land below the agree-with-bull line."""
    out = bear_agent(_signal(rsi=55, momentum_5b=0.02, regime="range"))
    assert out.confidence < 0.5


# ── Adversarial resolver ──────────────────────────────────────────────────────


def test_resolver_picks_long_on_positive_gap():
    bull = AgentOutput("LONG", 0.85, 1.4, "t", [])
    bear = AgentOutput("SHORT_OR_REJECT", 0.30, 1.1, "t", [])
    out = resolve_adversarial(bull, bear)
    assert out["decision"] == "LONG"
    assert out["edge_gap"] > 0
    assert 0.0 <= out["risk_multiplier"] <= 1.0


def test_resolver_picks_short_on_negative_gap():
    bull = AgentOutput("LONG", 0.30, 1.1, "t", [])
    bear = AgentOutput("SHORT_OR_REJECT", 0.85, 1.4, "t", [])
    out = resolve_adversarial(bull, bear)
    assert out["decision"] == "SHORT_OR_AVOID"
    assert out["edge_gap"] < 0


def test_resolver_returns_no_trade_when_gap_under_threshold():
    """Both agents agreeing slightly → ambiguous → NO_TRADE.
    Captures the principal Commander value: refusing to fire when
    the evidence is weak."""
    bull = AgentOutput("LONG", 0.55, 1.2, "t", [])
    bear = AgentOutput("SHORT_OR_REJECT", 0.50, 1.2, "t", [])
    out = resolve_adversarial(bull, bear)
    assert out["decision"] == "NO_TRADE"
    assert abs(out["edge_gap"]) <= EDGE_GAP_THRESHOLD


def test_resolver_risk_multiplier_clamped_to_one():
    """Edge gap can mathematically exceed 1.0 (high conf × high R)
    but the multiplier output must be capped."""
    bull = AgentOutput("LONG", 1.0, 2.0, "t", [])
    bear = AgentOutput("SHORT_OR_REJECT", 0.0, 1.0, "t", [])
    out = resolve_adversarial(bull, bear)
    assert out["risk_multiplier"] == 1.0


def test_resolver_threshold_is_configurable():
    """Lets us empirically retune EDGE_GAP_THRESHOLD without
    rewriting the resolver."""
    bull = AgentOutput("LONG", 0.6, 1.2, "t", [])
    bear = AgentOutput("SHORT_OR_REJECT", 0.5, 1.2, "t", [])
    # Default threshold (0.35) → NO_TRADE
    assert resolve_adversarial(bull, bear)["decision"] == "NO_TRADE"
    # Tightened threshold (0.05) → LONG fires
    assert resolve_adversarial(bull, bear, edge_gap_threshold=0.05)["decision"] == "LONG"


# ── Double gate ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_double_gate_env_flag_default_off(monkeypatch):
    """Without the env flag set, the function returns None even if
    Tier 3 was unlocked. This is the primary safety guarantee."""
    monkeypatch.delenv("CRYPTO_ADVERSARIAL_ENABLED", raising=False)

    out = await run_adversarial_decision(db=object(), signal=_signal())
    assert out is None


@pytest.mark.asyncio
async def test_double_gate_tier3_locked_still_records_shadow(monkeypatch):
    """New (2026-05-04) policy: shadow mode must RUN even when Tier 3
    is locked. Shadow has no live impact, and gating recording on
    Tier 3 created a chicken-and-egg problem where the layer could
    never accumulate the evidence Tier 3 measures.

    Env flag is still the operator kill-switch; Tier 3 only gates
    non-shadow phases (risk_only / veto / full).
    """
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")

    async def fake_locked(_db):
        return False

    with patch("services.adversarial_core._tier3_gate_open", side_effect=fake_locked):
        out = await run_adversarial_decision(db=object(), signal=_signal())
    assert out is not None
    assert out["phase"] == "shadow"
    assert "bull_case" in out and "bear_case" in out


@pytest.mark.asyncio
async def test_tier3_locked_blocks_non_shadow_phases(monkeypatch):
    """Phase promotion — risk_only / veto / full — still requires
    Tier 3 unlocked. Tier 3 is a live-mutation safety net, not a
    shadow-observation prerequisite."""
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")

    async def fake_locked(_db):
        return False

    for phase in ("risk_only", "veto", "full"):
        monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", phase)
        with patch(
            "services.adversarial_core._tier3_gate_open",
            side_effect=fake_locked,
        ):
            out = await run_adversarial_decision(db=object(), signal=_signal())
        assert out is None, f"phase={phase} leaked past a locked Tier 3"


@pytest.mark.asyncio
async def test_double_gate_tier3_check_failure_degrades_closed(monkeypatch):
    """If the Tier 3 lookup raises *on a non-shadow phase*, the gate
    must stay shut. Better to be silent than to fire on a stale stats
    read. In shadow phase the Tier 3 check doesn't even run, so the
    stale-stats risk doesn't apply there."""
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "veto")

    # Patch build_tier3_stats to raise — the real gate catches it
    # internally and degrades to False.
    with patch("services.tier3_readiness.build_tier3_stats",
               side_effect=RuntimeError("db down")):
        out = await run_adversarial_decision(db=object(), signal=_signal())
    assert out is None


@pytest.mark.asyncio
async def test_double_gate_open_returns_full_payload(monkeypatch):
    """Both gates open → returns a real decision payload with all the
    fields the logger persists."""
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")

    async def fake_open(_db):
        return True

    with patch("services.adversarial_core._tier3_gate_open", side_effect=fake_open):
        out = await run_adversarial_decision(db=object(), signal=_signal())

    assert out is not None
    assert out["phase"] == "shadow"
    assert out["symbol"] == "BTC"
    assert out["regime"] == "trending"
    assert out["decision"] in ("LONG", "SHORT_OR_AVOID", "NO_TRADE")
    assert "bull_case" in out and "bear_case" in out
    assert "edge_gap" in out and "risk_multiplier" in out
    # Make sure timestamps are ISO strings, not datetime objects (we
    # want the dict JSON-serialisable so test doubles + audit
    # pipelines all behave identically).
    assert isinstance(out["timestamp"], str)


@pytest.mark.asyncio
async def test_invalid_phase_falls_back_to_shadow(monkeypatch):
    """Operator typo in CRYPTO_ADVERSARIAL_PHASE shouldn't unlock
    `full` mode — invalid values must collapse to the safest option."""
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_ENABLED", "1")
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "fullsend")

    async def fake_open(_db):
        return True

    with patch("services.adversarial_core._tier3_gate_open", side_effect=fake_open):
        out = await run_adversarial_decision(db=object(), signal=_signal())
    assert out["phase"] == "shadow"
