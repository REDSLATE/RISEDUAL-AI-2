"""Tests — Named Kill-Switch Profiles (2026-02-26, P2)."""
from __future__ import annotations

import pytest

from services.kill_switch_profiles import (
    PROFILES,
    WARRIOR_SMALL_ACCOUNT,
    evaluate_profile,
    get_profile,
    list_profiles,
)


# ── Registry shape ────────────────────────────────────────────────


def test_warrior_profile_registered():
    assert WARRIOR_SMALL_ACCOUNT.key == "small_account_warrior"
    assert "warrior_trading" in WARRIOR_SMALL_ACCOUNT.tags
    assert WARRIOR_SMALL_ACCOUNT in PROFILES.values()


def test_list_profiles_payload_shape():
    payload = list_profiles()
    assert isinstance(payload, list) and len(payload) >= 1
    sample = payload[0]
    for key in ("key", "name", "description", "source", "rules", "tags"):
        assert key in sample
    for rule in (
        "daily_max_loss_pct",
        "daily_max_loss_usd",
        "consecutive_loss_limit",
        "daily_profit_cap_pct",
    ):
        assert rule in sample["rules"]


def test_get_profile_case_insensitive():
    assert get_profile("SMALL_ACCOUNT_WARRIOR") is WARRIOR_SMALL_ACCOUNT
    assert get_profile("  small_account_warrior  ") is WARRIOR_SMALL_ACCOUNT
    assert get_profile("does_not_exist") is None


# ── Warrior profile evaluator ─────────────────────────────────────


def test_warrior_quiet_day_no_halt():
    ev = evaluate_profile(
        "small_account_warrior",
        starting_equity_usd=1000.0,
        realized_pnl_usd_today=12.50,
        consecutive_losses_today=1,
    )
    assert ev.halt is False
    assert ev.triggers == ()


def test_warrior_daily_max_loss_pct_trips_at_minus_10pct():
    # -10% of $1,000 = -$100 → both pct AND hard USD floor are hit
    # simultaneously, so the evaluator MUST surface both triggers.
    ev = evaluate_profile(
        "small_account_warrior",
        starting_equity_usd=1000.0,
        realized_pnl_usd_today=-100.01,
        consecutive_losses_today=0,
    )
    assert ev.halt is True
    rules = {t.rule for t in ev.triggers}
    assert "rule_2_daily_max_loss_pct" in rules
    assert "rule_2_daily_max_loss_usd" in rules


def test_warrior_daily_max_loss_usd_floor_protects_tiny_accounts():
    # $200 account at -$100 is only -50% pct-wise, but the absolute
    # USD floor of -$100 still trips. This is the toolkit's
    # belt-and-suspenders behaviour the PDF documents.
    ev = evaluate_profile(
        "small_account_warrior",
        starting_equity_usd=200.0,
        realized_pnl_usd_today=-100.0,
        consecutive_losses_today=0,
    )
    assert ev.halt is True
    rules = {t.rule for t in ev.triggers}
    assert "rule_2_daily_max_loss_usd" in rules


def test_warrior_three_consecutive_losses_halts():
    ev = evaluate_profile(
        "small_account_warrior",
        starting_equity_usd=5000.0,
        realized_pnl_usd_today=-25.0,
        consecutive_losses_today=3,
    )
    assert ev.halt is True
    assert any(t.rule == "rule_3_consecutive_losses" for t in ev.triggers)


def test_warrior_two_consecutive_losses_does_not_halt():
    ev = evaluate_profile(
        "small_account_warrior",
        starting_equity_usd=5000.0,
        realized_pnl_usd_today=-30.0,
        consecutive_losses_today=2,
    )
    assert ev.halt is False


def test_warrior_no_profit_cap_so_winning_session_never_halts():
    # Toolkit explicitly says "don't stop until momentum cools" — so
    # a +50% session does NOT trip the profile.
    ev = evaluate_profile(
        "small_account_warrior",
        starting_equity_usd=1000.0,
        realized_pnl_usd_today=+500.0,
        consecutive_losses_today=0,
    )
    assert ev.halt is False


def test_evaluator_rejects_unknown_profile():
    with pytest.raises(ValueError):
        evaluate_profile(
            "nope",
            starting_equity_usd=1000.0,
            realized_pnl_usd_today=0.0,
            consecutive_losses_today=0,
        )


def test_evaluation_inputs_are_preserved():
    ev = evaluate_profile(
        "small_account_warrior",
        starting_equity_usd=2500.0,
        realized_pnl_usd_today=-50.0,
        consecutive_losses_today=2,
    )
    assert ev.inputs == {
        "starting_equity_usd": 2500.0,
        "realized_pnl_usd_today": -50.0,
        "consecutive_losses_today": 2,
    }
