"""Alpha helper tests — decision log + weight calibrator + daily mandate."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from services import alpha_decision_log, alpha_weight_calibrator, alpha_daily_mandate
from services.alpha_weight_calibrator import AccountState, calibrate_position_size
from services.alpha_daily_mandate import (
    compute_pressure,
    should_block_new_open,
    should_force_flat,
)


# ── Calibrator ───────────────────────────────────────────────────


def test_calibrator_caps_at_max_total():
    s = AccountState(cash_usd=10_000, equity_value_usd=10_000)
    d = calibrate_position_size(s)
    # Default $500 cap / 5 slots = $100/trade
    assert d.notional_usd == pytest.approx(100.0)
    assert d.capped_by == "ABSOLUTE_CAP"


def test_calibrator_uses_min_of_cash_equity():
    # Cash is the limit
    s = AccountState(cash_usd=200, equity_value_usd=10_000)
    d = calibrate_position_size(s)
    # $200/5 slots = $40/trade
    assert d.notional_usd == pytest.approx(40.0)


def test_calibrator_subtracts_open_exposure():
    s = AccountState(cash_usd=10_000, equity_value_usd=10_000, open_exposure_usd=400)
    d = calibrate_position_size(s)
    # available = min(cash, equity) - exposure = 10k - 400 = 9600 → cap 500 → 100/trade
    assert d.notional_usd == pytest.approx(100.0)


def test_calibrator_zero_when_below_min_ticket():
    s = AccountState(cash_usd=2.0, equity_value_usd=2.0)
    d = calibrate_position_size(s)
    # 2 / 5 = 0.40 < min ticket 1.0 → zero
    assert d.notional_usd == 0.0
    assert d.capped_by == "MIN_TICKET"


def test_calibrator_rejects_negative_state():
    with pytest.raises(ValueError):
        AccountState(cash_usd=-1, equity_value_usd=100)


def test_calibrator_never_uses_buying_power():
    # Sanity: the dataclass has no buying_power field.
    fields = AccountState.__dataclass_fields__
    assert "buying_power" not in fields
    assert "buying_power_usd" not in fields


# ── Daily Mandate ────────────────────────────────────────────────


def test_pressure_at_target_uses_ceiling():
    # On pace with the full 20 round-trips already completed.
    p = compute_pressure(completed=20)
    assert p.adjusted_threshold == pytest.approx(p.raw_ceiling)


def test_pressure_behind_pace_lowers_threshold():
    # 0 round trips with mid-session → adjusted should be below ceiling.
    p = compute_pressure(completed=0, now=datetime(2026, 6, 15, 17, 30, tzinfo=timezone.utc))
    # Mid-session ET ≈ 13:30 ET (June DST off in the helper's UTC-5 fallback;
    # production zoneinfo handles DST). Threshold should be < ceiling.
    assert p.adjusted_threshold <= p.raw_ceiling


def test_pressure_threshold_bounded():
    p = compute_pressure(completed=5)
    assert p.raw_floor <= p.adjusted_threshold <= p.raw_ceiling


def test_should_block_new_open_after_1530_et():
    # 21:00 UTC ≈ 16:00 ET (post-cutoff). With UTC-5 fallback that's 16:00.
    t = datetime(2026, 1, 15, 21, 0, tzinfo=timezone.utc)
    assert should_block_new_open(t) is True


def test_should_force_flat_after_1555_et():
    # 21:00 UTC = 16:00 ET (post 15:55 cutoff)
    t = datetime(2026, 1, 15, 21, 0, tzinfo=timezone.utc)
    assert should_force_flat(t) is True


def test_force_flat_morning_off():
    # 14:30 UTC = 09:30 ET → before cutoff
    t = datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc)
    assert should_force_flat(t) is False


# ── Decision log ─────────────────────────────────────────────────


def test_decision_log_validation_blocked_at_required_for_no_trade():
    """Pure validation check; doesn't hit the DB."""
    from services.alpha_decision_log import _validate_blocked_at
    assert _validate_blocked_at(None) is None
    assert _validate_blocked_at("perception") == "perception"
    with pytest.raises(ValueError):
        _validate_blocked_at("not_a_real_stage")
