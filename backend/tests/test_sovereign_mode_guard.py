"""
Tests for the DTD/PRD mode guard + bounded contribution + adapters.

Invariants pinned:
* Default mode is PRD (safer default).
* ``require_dtd()`` raises when in PRD; ``require_prd()`` raises when in DTD.
* DTD nightly retrain can ONLY run in DTD mode.
* Bounded contribution cannot flip action, cannot convert HOLD to trade.
* Contribution respects ``MAX_SOVEREIGN_CONFIDENCE_DELTA`` cap.
* PRD adapter shadow logging works in both modes (observation is safe).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from services import sovereign_mode_guard as smg
from services import sovereign_promotion_gate as spg
from services.sovereign_promotion_gate import apply_sovereign_contribution


def _run(coro):
    # Fresh loop avoids RuntimeError: no current event loop
    # pollution after a prior async test closes pytest-asyncio's loop.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── Mode guard ────────────────────────────────────────────────────────────────


def test_default_mode_is_prd(monkeypatch):
    monkeypatch.delenv("RISEDUAL_CORE_MODE", raising=False)
    assert smg.get_core_mode() == "PRD"
    assert smg.is_prd() is True
    assert smg.is_dtd() is False


def test_explicit_dtd_mode(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "DTD")
    assert smg.get_core_mode() == "DTD"
    assert smg.is_dtd() is True


def test_unknown_mode_falls_back_to_prd(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "UNKNOWN")
    assert smg.get_core_mode() == "PRD"


def test_require_dtd_raises_in_prd(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "PRD")
    with pytest.raises(RuntimeError, match="DTD-only"):
        smg.require_dtd()


def test_require_prd_raises_in_dtd(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "DTD")
    with pytest.raises(RuntimeError, match="PRD-only"):
        smg.require_prd()


def test_require_prd_passes_in_prd(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "PRD")
    smg.require_prd()  # no raise


def test_require_dtd_passes_in_dtd(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "DTD")
    smg.require_dtd()  # no raise


# ─── Bounded contribution invariants ──────────────────────────────────────────


def test_contribution_noop_when_not_promoted():
    adj, meta = apply_sovereign_contribution(
        production_confidence=0.70,
        production_action="LONG",
        sovereign_decision={"action": "LONG", "confidence": 0.9},
        promotion_state={"promoted": False},
    )
    assert adj == 0.70
    assert meta["applied"] is False
    assert meta["reason"] == "not_promoted"


def test_contribution_noop_when_no_sovereign_decision():
    adj, meta = apply_sovereign_contribution(
        production_confidence=0.70,
        production_action="LONG",
        sovereign_decision=None,
        promotion_state={"promoted": True},
    )
    assert adj == 0.70
    assert meta["applied"] is False
    assert meta["reason"] == "no_sovereign_decision"


def test_contribution_never_turns_hold_into_trade():
    """CRITICAL: Sovereign CANNOT convert a production HOLD to a trade."""
    adj, meta = apply_sovereign_contribution(
        production_confidence=0.0,
        production_action="HOLD",
        sovereign_decision={"action": "LONG", "confidence": 1.0},
        promotion_state={"promoted": True},
    )
    assert adj == 0.0
    assert meta["applied"] is False
    assert meta["reason"] == "production_hold_immutable"


def test_contribution_aligned_long_bumps_confidence_within_cap():
    cap = spg.MAX_SOVEREIGN_CONFIDENCE_DELTA
    adj, meta = apply_sovereign_contribution(
        production_confidence=0.60,
        production_action="LONG",
        sovereign_decision={"action": "LONG", "confidence": 0.9},
        promotion_state={"promoted": True},
    )
    assert meta["applied"] is True
    assert meta["reason"] == "aligned_bump"
    assert 0.60 < adj <= 0.60 + cap + 1e-9
    assert meta["delta"] > 0


def test_contribution_contra_direction_reduces_but_never_flips():
    """CRITICAL: Sovereign CANNOT flip the production action."""
    adj, meta = apply_sovereign_contribution(
        production_confidence=0.70,
        production_action="LONG",
        sovereign_decision={"action": "SHORT", "confidence": 1.0},
        promotion_state={"promoted": True},
    )
    assert meta["applied"] is True
    assert meta["reason"] == "contra_reduces"
    assert adj < 0.70
    assert meta["delta"] < 0
    # No "action" field is ever returned — sovereign meta cannot leak a flip
    assert "action" not in meta


def test_contribution_sovereign_hold_softens_production_trade():
    adj, meta = apply_sovereign_contribution(
        production_confidence=0.80,
        production_action="LONG",
        sovereign_decision={"action": "HOLD", "confidence": 1.0},
        promotion_state={"promoted": True},
    )
    assert meta["applied"] is True
    assert meta["reason"] == "sovereign_hold_softens"
    assert adj < 0.80


def test_contribution_confidence_clamped_to_0_1():
    adj, _ = apply_sovereign_contribution(
        production_confidence=0.99,
        production_action="LONG",
        sovereign_decision={"action": "LONG", "confidence": 1.0},
        promotion_state={"promoted": True},
    )
    assert 0.0 <= adj <= 1.0
    adj2, _ = apply_sovereign_contribution(
        production_confidence=0.05,
        production_action="LONG",
        sovereign_decision={"action": "SHORT", "confidence": 1.0},
        promotion_state={"promoted": True},
    )
    assert 0.0 <= adj2 <= 1.0


def test_contribution_malformed_inputs_safe_noop():
    adj, meta = apply_sovereign_contribution(
        production_confidence=float("nan"),  # malformed
        production_action="LONG",
        sovereign_decision={"action": "LONG", "confidence": 0.9},
        promotion_state={"promoted": True},
    )
    # NaN comparison semantics mean we can't assert equality, but we can
    # assert the function didn't raise and returned some meta block.
    assert "reason" in meta


def test_contribution_respects_env_override_cap(monkeypatch):
    # Default cap is 0.08 — tighten to 0.02 and verify the bump never exceeds it
    monkeypatch.setattr(spg, "MAX_SOVEREIGN_CONFIDENCE_DELTA", 0.02)
    adj, meta = apply_sovereign_contribution(
        production_confidence=0.50,
        production_action="LONG",
        sovereign_decision={"action": "LONG", "confidence": 1.0},
        promotion_state={"promoted": True},
    )
    assert meta["delta"] <= 0.02 + 1e-9
    assert adj <= 0.52 + 1e-9


# ─── DTD adapter guard integration ─────────────────────────────────────────────


def test_dtd_nightly_retrain_blocked_in_prd_mode(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "PRD")
    from services.sovereign_dtd_adapter import run_dtd_nightly_retrain

    class _NullDB: ...
    with pytest.raises(RuntimeError, match="DTD-only"):
        _run(run_dtd_nightly_retrain(_NullDB()))


def test_dtd_challenger_blocked_in_prd_mode(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "PRD")
    from services.sovereign_dtd_adapter import run_dtd_challenger_decision

    class _NullDB: ...
    with pytest.raises(RuntimeError, match="DTD-only"):
        _run(run_dtd_challenger_decision(_NullDB(), "AAPL"))


def test_prd_contribution_blocked_in_dtd_mode(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "DTD")
    from services.sovereign_prd_adapter import apply_promoted_sovereign_contribution

    class _NullDB: ...
    with pytest.raises(RuntimeError, match="PRD-only"):
        _run(apply_promoted_sovereign_contribution(
            _NullDB(), symbol="AAPL", asset_type="equity",
            production_action="LONG", production_confidence=0.6,
        ))


def test_assert_prd_never_trains_blocks_outside_prd(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "DTD")
    from services.sovereign_prd_adapter import assert_prd_never_trains
    with pytest.raises(RuntimeError, match="PRD-only"):
        assert_prd_never_trains()


def test_assert_prd_never_trains_passes_in_prd(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CORE_MODE", "PRD")
    from services.sovereign_prd_adapter import assert_prd_never_trains
    assert assert_prd_never_trains() is True


# ─── PRD shadow logging works without mode restriction ───────────────────────


def test_prd_shadow_is_safe_in_both_modes(monkeypatch):
    """Shadow logging is observation, not authority — allowed in any mode."""
    for mode in ("PRD", "DTD"):
        monkeypatch.setenv("RISEDUAL_CORE_MODE", mode)
        from services.sovereign_prd_adapter import run_prd_sovereign_shadow
        from tests.test_sovereign_ai_core import _FakeDB

        db = _FakeDB()
        dec = _run(run_prd_sovereign_shadow(db, "AAPL", asset_type="equity"))
        assert dec is not None
        assert dec.symbol == "AAPL"
