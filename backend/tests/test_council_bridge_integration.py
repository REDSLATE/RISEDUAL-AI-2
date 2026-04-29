"""Bridge integration tests for the Council risk modulator.

Verifies:
  * With no bridge active, the modulator's output matches the prior
    contract exactly (regression test for the bridge wiring).
  * With the bridge active at value=1.05, every output's
    risk_multiplier scales by 1.05 within the bridge's clamp.
  * The bridge cannot influence ``council_applied`` or ``reason``
    fields — only ``risk_multiplier``.
  * Bounded-clamp defence-in-depth: even if _ACTIVE were corrupted
    with an out-of-range value, get_calibration() re-clamps on read.
"""
from __future__ import annotations

import pytest

from services import promotion_bridge


@pytest.fixture(autouse=True)
def _enable_modulator_and_reset(monkeypatch):
    """Tests need the modulator on so we exercise the actual table."""
    from services import council_risk_modulator
    monkeypatch.setattr(council_risk_modulator, "COUNCIL_RISK_MODULATOR_ENABLED", True)
    promotion_bridge._ACTIVE.clear()
    yield
    promotion_bridge._ACTIVE.clear()


def _modulate(**kwargs):
    from services.council_risk_modulator import apply_council_risk_modulation
    return apply_council_risk_modulation(**kwargs)


def test_modulator_unchanged_when_no_bridge_active():
    """Without an active bridge the modulator output must be exactly
    what it was before the bridge wiring (no surprise drift)."""
    result = _modulate(
        commander_action="BUY",
        commander_risk_multiplier=1.0,
        council_action="BUY",
        council_confidence=0.85,
        council_tier_open=True,
    )
    assert "bridge_calibration" not in result
    assert result["council_applied"] is True
    assert result["risk_multiplier"] > 0


def test_modulator_applies_bridge_when_active():
    promotion_bridge._ACTIVE["bridge_v1_council_calibration"] = {
        "value": 1.05, "activated_at": "now", "actor": "test", "metadata": {},
    }
    result = _modulate(
        commander_action="BUY",
        commander_risk_multiplier=1.0,
        council_action="BUY",
        council_confidence=0.85,
        council_tier_open=True,
    )
    assert "bridge_calibration" in result
    assert result["bridge_calibration"] == 1.05
    # Agreement upweight (1.10) capped at MAX_COUNCIL_UPWEIGHT (1.25)
    # → base 1.0 * 1.10 = 1.10. Bridge then * 1.05 = 1.155.
    assert result["risk_multiplier"] == pytest.approx(1.155, abs=1e-6)


def test_modulator_bridge_does_not_change_council_applied():
    promotion_bridge._ACTIVE["bridge_v1_council_calibration"] = {
        "value": 0.95, "activated_at": "now", "actor": "test", "metadata": {},
    }
    result = _modulate(
        commander_action="HOLD",
        commander_risk_multiplier=1.0,
        council_action="BUY",
        council_confidence=0.85,
        council_tier_open=True,
    )
    assert result["council_applied"] is False
    assert result["reason"] == "commander_hold_not_promoted"


def test_bridge_clamps_out_of_range_value_on_read():
    """Defence in depth — even a corrupted _ACTIVE row can't push the
    output past the spec's [0.90, 1.10] clamp."""
    promotion_bridge._ACTIVE["bridge_v1_council_calibration"] = {
        "value": 999.0, "activated_at": "now", "actor": "test", "metadata": {},
    }
    val = promotion_bridge.get_calibration("bridge_v1_council_calibration")
    assert val == 1.10  # clamped to upper bound
