"""
Tests for the symbol failure memory service.

Covers:
* Pure penalty calculation matrix (each tier independently + composition)
* Mongo-backed lookups (recent N + 7-day window)
* Direction filtering on recent-N
* Force-HOLD short-circuit at 4+ recent losses
* Composition of confidence and size multipliers
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.symbol_failure_memory import (
    DEFAULT_RECENT_WINDOW,
    FailurePenalty,
    compute_failure_penalty,
    get_failure_penalty,
)
from tests.test_sovereign_ai_core import _FakeDB


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── Pure penalty tests ───────────────────────────────────────────────────────


def test_no_failures_no_penalty():
    p = compute_failure_penalty(recent_losses=0, misses_7d=0)
    c, s = p.apply(confidence=0.7, size_multiplier=1.0)
    assert c == 0.7
    assert s == 1.0
    assert p.force_hold is False
    assert "no_recent_failures" in p.reason


def test_one_recent_loss_no_penalty():
    p = compute_failure_penalty(recent_losses=1, misses_7d=1)
    c, s = p.apply(confidence=0.7, size_multiplier=1.0)
    assert c == 0.7
    assert s == 1.0


def test_two_recent_losses_confidence_multiplied_by_07():
    p = compute_failure_penalty(recent_losses=2, misses_7d=0)
    c, _ = p.apply(confidence=0.8, size_multiplier=1.0)
    assert abs(c - 0.8 * 0.7) < 1e-6


def test_three_recent_losses_confidence_multiplied_by_05():
    p = compute_failure_penalty(recent_losses=3, misses_7d=0)
    c, _ = p.apply(confidence=0.8, size_multiplier=1.0)
    assert abs(c - 0.8 * 0.5) < 1e-6


def test_four_recent_losses_force_hold_zeros_everything():
    p = compute_failure_penalty(recent_losses=4, misses_7d=0)
    assert p.force_hold is True
    c, s = p.apply(confidence=0.9, size_multiplier=1.0)
    assert c == 0.0
    assert s == 0.0


def test_two_misses_7d_halves_size_multiplier():
    p = compute_failure_penalty(recent_losses=0, misses_7d=2)
    _, s = p.apply(confidence=0.6, size_multiplier=1.0)
    assert s == 0.5


def test_three_misses_7d_caps_confidence_at_075():
    p = compute_failure_penalty(recent_losses=0, misses_7d=3)
    c, _ = p.apply(confidence=0.95, size_multiplier=1.0)
    assert c == 0.75


def test_three_misses_7d_doesnt_lift_low_confidence():
    """The cap is a ceiling, not a floor — low confidence stays low."""
    p = compute_failure_penalty(recent_losses=0, misses_7d=3)
    c, _ = p.apply(confidence=0.4, size_multiplier=1.0)
    assert c == 0.4


def test_composes_recent_n_and_7d_branches():
    """3 recent + 4 in 7d → conf*0.5 then capped at 0.75, size*0.5."""
    p = compute_failure_penalty(recent_losses=3, misses_7d=4)
    c, s = p.apply(confidence=0.95, size_multiplier=1.0)
    # 0.95 * 0.5 = 0.475, then cap 0.75 wouldn't bind (0.475 < 0.75)
    assert abs(c - 0.475) < 1e-6
    assert s == 0.5


def test_force_hold_dominates_all_other_branches():
    """4 recent losses force HOLD even if 7d also has hits."""
    p = compute_failure_penalty(recent_losses=4, misses_7d=5)
    c, s = p.apply(confidence=0.9, size_multiplier=1.0)
    assert c == 0.0
    assert s == 0.0


def test_apply_clamps_to_unit_interval():
    p = compute_failure_penalty(recent_losses=0, misses_7d=0)
    c, s = p.apply(confidence=2.0, size_multiplier=5.0)
    assert c == 1.0
    assert s == 1.0


# ─── Mongo-backed lookup tests ────────────────────────────────────────────────


def _seed_trade(
    db: _FakeDB, *,
    symbol: str,
    coll: str,
    outcome: str,
    direction: str,
    age: timedelta,
) -> None:
    now = datetime.now(timezone.utc)
    field = "ticker" if coll == "paper_trades" else "symbol"
    db[coll].docs.append({
        field: symbol,
        "direction": direction,
        "status": "closed",
        "outcome": outcome,
        "closed_at": now - age,
    })


def test_get_failure_penalty_counts_recent_losses_correctly():
    db = _FakeDB()
    # 3 recent losses, 1 win
    for _ in range(3):
        _seed_trade(db, symbol="NVDA", coll="paper_trades",
                    outcome="loss", direction="up", age=timedelta(hours=1))
    _seed_trade(db, symbol="NVDA", coll="paper_trades",
                outcome="win", direction="up", age=timedelta(hours=2))
    result = _run(get_failure_penalty(db, symbol="NVDA", direction="up"))
    assert result.recent_losses == 3
    assert result.confidence_multiplier == 0.5


def test_get_failure_penalty_force_hold_at_four_recent_losses():
    db = _FakeDB()
    for _ in range(4):
        _seed_trade(db, symbol="NVDA", coll="paper_trades",
                    outcome="loss", direction="up", age=timedelta(hours=1))
    result = _run(get_failure_penalty(db, symbol="NVDA", direction="up"))
    assert result.force_hold is True


def test_get_failure_penalty_filters_by_direction():
    """Loss on LONG shouldn't penalise a fresh SHORT thesis (recent-N branch)."""
    db = _FakeDB()
    for _ in range(4):
        _seed_trade(db, symbol="NVDA", coll="paper_trades",
                    outcome="loss", direction="up", age=timedelta(hours=1))
    # Asking about SHORT direction
    result = _run(get_failure_penalty(db, symbol="NVDA", direction="down"))
    assert result.recent_losses == 0
    assert result.force_hold is False
    # But 7d window is direction-agnostic — those 4 LONG losses still count
    assert result.misses_7d == 4


def test_get_failure_penalty_handles_crypto_collection():
    db = _FakeDB()
    for _ in range(2):
        _seed_trade(db, symbol="BTCUSDT", coll="crypto_paper_trades",
                    outcome="loss", direction="LONG", age=timedelta(hours=1))
    result = _run(get_failure_penalty(
        db, symbol="BTCUSDT", direction="LONG", asset_type="crypto",
    ))
    assert result.recent_losses == 2
    assert result.confidence_multiplier == 0.7


def test_get_failure_penalty_null_db_safe():
    result = _run(get_failure_penalty(None, symbol="NVDA"))
    assert result.recent_losses == 0
    assert result.force_hold is False


def test_get_failure_penalty_excludes_old_misses_from_7d_window():
    db = _FakeDB()
    # 1 loss right now
    _seed_trade(db, symbol="NVDA", coll="paper_trades",
                outcome="loss", direction="up", age=timedelta(hours=1))
    # 3 losses 30 days ago (outside 7d window)
    for _ in range(3):
        _seed_trade(db, symbol="NVDA", coll="paper_trades",
                    outcome="loss", direction="up", age=timedelta(days=30))
    result = _run(get_failure_penalty(db, symbol="NVDA", direction="up"))
    # 7d count should only see the 1 recent loss
    assert result.misses_7d == 1


def test_get_failure_penalty_excludes_open_trades():
    """Only closed trades count — open positions are not yet failures."""
    db = _FakeDB()
    db["paper_trades"].docs.append({
        "ticker": "NVDA",
        "direction": "up",
        "status": "open",  # not closed
        "outcome": None,
        "closed_at": None,
    })
    result = _run(get_failure_penalty(db, symbol="NVDA", direction="up"))
    assert result.recent_losses == 0


def test_returned_dataclass_is_immutable():
    """FailurePenalty is frozen — can't be mutated after creation."""
    p = compute_failure_penalty(recent_losses=2, misses_7d=2)
    with pytest.raises((AttributeError, Exception)):
        p.recent_losses = 99  # type: ignore[misc]
