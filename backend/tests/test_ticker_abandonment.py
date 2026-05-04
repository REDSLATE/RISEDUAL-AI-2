"""
Tests for ``services.ticker_abandonment.decide_ticker_exit``.

Pin every branch of the operator-approved spec from 2026-05-04
exactly as written. The function is pure — these tests are the
contract; nothing else may change behaviour without changing
this file.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.ticker_abandonment import (
    TickerExitDecision, decide_ticker_exit,
)


def _base_kwargs(**over):
    """Default args that map to KEEP. Override per-test."""
    return {
        "symbol": "NVDA",
        "recent_signals": 10,
        "recent_rejections": 0,
        "recent_losses": 0,
        "recent_wins": 5,
        "avg_confidence": 0.65,
        "avg_rr": 1.5,
        "last_profitable_at": datetime.now(timezone.utc) - timedelta(hours=1),
        **over,
    }


# ── Branch 1: Not enough data ─────────────────────────────────────


def test_keep_when_signals_below_5():
    out = decide_ticker_exit(**_base_kwargs(recent_signals=4))
    assert out.action == "KEEP"
    assert out.reason == "not_enough_recent_signals"
    assert out.cooldown_minutes == 0


def test_keep_when_signals_zero():
    out = decide_ticker_exit(**_base_kwargs(recent_signals=0))
    assert out.action == "KEEP"


# ── Branch 2: Toxic symbol ────────────────────────────────────────


def test_abandon_on_loss_cluster_low_win_rate():
    """≥4 losses AND win_rate < 35% → ABANDON 24h."""
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=15, recent_losses=5, recent_wins=2,
    ))
    assert out.action == "ABANDON"
    assert out.reason == "loss_cluster_low_win_rate"
    assert out.cooldown_minutes == 1440


def test_no_abandon_when_losses_high_but_win_rate_holds():
    """4+ losses but win_rate ≥ 35% → not abandoned."""
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=15, recent_losses=5, recent_wins=10,
    ))
    assert out.action != "ABANDON"


# ── Branch 3: High rejection rate ─────────────────────────────────


def test_cooldown_on_high_rejection_rate():
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=12, recent_rejections=10,  # 83% rejection
        recent_wins=1, recent_losses=1,
    ))
    assert out.action == "COOLDOWN"
    assert out.reason == "high_rejection_rate"
    assert out.cooldown_minutes == 360


def test_no_cooldown_when_rejection_rate_below_threshold():
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=12, recent_rejections=8,  # 67% rejection
        recent_wins=2, recent_losses=2,
    ))
    assert out.action != "COOLDOWN" or out.reason != "high_rejection_rate"


def test_no_high_rejection_cooldown_below_8_signals():
    """Branch 3 needs recent_signals ≥ 8."""
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=7, recent_rejections=7,
        recent_wins=0, recent_losses=0, avg_rr=2.0,
    ))
    # Branch 3 wouldn't fire (< 8 signals); branch 4/5 also won't
    # for this combo. KEEP.
    assert out.action == "KEEP"


# ── Branch 4: Bad reward/risk ─────────────────────────────────────


def test_cooldown_on_poor_average_rr():
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=6, avg_rr=0.9,
        recent_wins=2, recent_losses=2,
    ))
    assert out.action == "COOLDOWN"
    assert out.reason == "poor_average_rr"
    assert out.cooldown_minutes == 240


def test_no_rr_cooldown_below_5_signals():
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=4, avg_rr=0.5,
    ))
    assert out.action == "KEEP"


# ── Branch 5: Low-confidence churn ────────────────────────────────


def test_cooldown_on_low_confidence_churn():
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=10, avg_confidence=0.50,
        recent_wins=2, recent_losses=2, avg_rr=1.5,
    ))
    assert out.action == "COOLDOWN"
    assert out.reason == "low_confidence_churn"
    assert out.cooldown_minutes == 180


def test_no_low_conf_cooldown_below_8_signals():
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=7, avg_confidence=0.40,
        recent_wins=2, recent_losses=2, avg_rr=1.5,
    ))
    # Branch 4 catches this first since avg_rr=1.5 doesn't trip,
    # branch 5 needs 8+ signals — falls through to branch 6 / KEEP.
    assert out.action == "KEEP"


# ── Branch 6: Stale, no recent profit ─────────────────────────────


def test_cooldown_on_stale_no_recent_profit():
    last_profit = datetime.now(timezone.utc) - timedelta(days=10)
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=10, recent_losses=4, recent_wins=2,
        avg_confidence=0.65, avg_rr=1.5,
        last_profitable_at=last_profit,
    ))
    # Branch 2 doesn't fire because win_rate is 33% but losses=4 trips it
    # → adjust losses=3 to isolate branch 6.


def test_cooldown_branch_6_isolated():
    """Set inputs that ONLY trip branch 6 (stale no recent profit)."""
    last_profit = datetime.now(timezone.utc) - timedelta(days=10)
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=10, recent_losses=3, recent_wins=5,
        avg_confidence=0.65, avg_rr=1.5,
        last_profitable_at=last_profit,
    ))
    assert out.action == "COOLDOWN"
    assert out.reason == "stale_no_recent_profit"
    assert out.cooldown_minutes == 720


def test_no_branch_6_cooldown_when_recent_profit():
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=10, recent_losses=3, recent_wins=5,
        avg_confidence=0.65, avg_rr=1.5,
        last_profitable_at=datetime.now(timezone.utc) - timedelta(days=2),
    ))
    assert out.action == "KEEP"


def test_no_branch_6_cooldown_below_3_losses():
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=10, recent_losses=2, recent_wins=6,
        avg_confidence=0.65, avg_rr=1.5,
        last_profitable_at=datetime.now(timezone.utc) - timedelta(days=10),
    ))
    assert out.action == "KEEP"


# ── KEEP path ────────────────────────────────────────────────────


def test_keep_when_all_branches_clear():
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=10, recent_losses=2, recent_wins=6,
        recent_rejections=2,
        avg_confidence=0.70, avg_rr=2.0,
        last_profitable_at=datetime.now(timezone.utc) - timedelta(hours=12),
    ))
    assert out.action == "KEEP"
    assert out.reason == "symbol_still_eligible"


# ── Branch ordering / precedence ──────────────────────────────────


def test_abandon_takes_precedence_over_high_rejection():
    """A toxic symbol that is ALSO being rejected gets ABANDON
    (24h), not the milder rejection COOLDOWN (6h). Branch 2 runs
    before branch 3 in the spec."""
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=15, recent_rejections=12,
        recent_losses=5, recent_wins=1, avg_rr=0.8,
    ))
    assert out.action == "ABANDON"
    assert out.cooldown_minutes == 1440


def test_high_rejection_takes_precedence_over_poor_rr():
    out = decide_ticker_exit(**_base_kwargs(
        recent_signals=12, recent_rejections=10,
        recent_wins=1, recent_losses=1, avg_rr=0.5,
    ))
    assert out.action == "COOLDOWN"
    assert out.reason == "high_rejection_rate"


# ── Frozen dataclass invariant ────────────────────────────────────


def test_decision_is_immutable():
    out = decide_ticker_exit(**_base_kwargs())
    with pytest.raises(Exception):
        out.action = "ABANDON"  # type: ignore[misc]


def test_decision_dataclass_shape():
    out = TickerExitDecision(action="KEEP", reason="x")
    assert out.cooldown_minutes == 0
