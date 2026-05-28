"""Tests — additional named profiles (2026-02-26).

Pinned doctrine: every registered profile must have the documented
shape; the new profiles' specific rules match their charter.
"""
from __future__ import annotations

import pytest

from services.kill_switch_profiles import (
    CONSERVATIVE_IRA,
    INTRADAY_MOMENTUM,
    PROFILES,
    SWING_TRADER,
    evaluate_profile,
)


def test_registry_has_four_profiles():
    keys = set(PROFILES.keys())
    assert {
        "small_account_warrior",
        "intraday_momentum",
        "swing_trader",
        "conservative_ira",
    } <= keys


def test_intraday_profit_cap_halts_winning_day():
    # Intraday Momentum has a 15% profit cap — winning past it should
    # trigger the give-back guard.
    ev = evaluate_profile(
        INTRADAY_MOMENTUM.key,
        starting_equity_usd=10_000.0,
        realized_pnl_usd_today=1_600.0,  # 16% > 15% cap
        consecutive_losses_today=0,
    )
    assert ev.halt is True
    assert any(t.rule == "profit_cap_pct" for t in ev.triggers)


def test_intraday_two_loss_streak_halts():
    ev = evaluate_profile(
        INTRADAY_MOMENTUM.key,
        starting_equity_usd=10_000.0,
        realized_pnl_usd_today=-50.0,
        consecutive_losses_today=2,
    )
    assert ev.halt is True
    assert any(t.rule == "rule_3_consecutive_losses" for t in ev.triggers)


def test_swing_tolerates_one_day_drawdown_below_15pct():
    # Swing is intentionally looser: 14% on the day shouldn't halt.
    ev = evaluate_profile(
        SWING_TRADER.key,
        starting_equity_usd=10_000.0,
        realized_pnl_usd_today=-1_400.0,
        consecutive_losses_today=3,  # also below the 5-loss cap
    )
    assert ev.halt is False


def test_swing_five_loss_streak_halts():
    ev = evaluate_profile(
        SWING_TRADER.key,
        starting_equity_usd=10_000.0,
        realized_pnl_usd_today=-200.0,
        consecutive_losses_today=5,
    )
    assert ev.halt is True
    assert any(t.rule == "rule_3_consecutive_losses" for t in ev.triggers)


def test_conservative_ira_tight_pct_floor():
    # 3% on a $20k account = -$600; we're at -$650, must halt by pct.
    ev = evaluate_profile(
        CONSERVATIVE_IRA.key,
        starting_equity_usd=20_000.0,
        realized_pnl_usd_today=-650.0,
        consecutive_losses_today=1,
    )
    assert ev.halt is True
    assert any(t.rule == "rule_2_daily_max_loss_pct" for t in ev.triggers)


def test_conservative_ira_hard_usd_floor_protects_big_accounts():
    # $100k IRA — 3% would be -$3k, but the hard $500 floor trips first.
    ev = evaluate_profile(
        CONSERVATIVE_IRA.key,
        starting_equity_usd=100_000.0,
        realized_pnl_usd_today=-600.0,
        consecutive_losses_today=0,
    )
    assert ev.halt is True
    rules = {t.rule for t in ev.triggers}
    assert "rule_2_daily_max_loss_usd" in rules


def test_conservative_ira_has_no_profit_cap():
    ev = evaluate_profile(
        CONSERVATIVE_IRA.key,
        starting_equity_usd=20_000.0,
        realized_pnl_usd_today=+5_000.0,
        consecutive_losses_today=0,
    )
    assert ev.halt is False  # retirement is a marathon — gains don't halt
