"""
Tests for the Sovereign AI resolution loop + narrator.

Covers:
* Resolution loop — joins decisions to paper_trades via sovereign_decision_id,
  back-patches outcomes at the right horizon, is idempotent, skips rows with
  no linked trade (per operator policy).
* Narrator — deterministic fallback always returns 3 bullets, cache round
  trip, missing-decision safe error.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.sovereign_resolution_loop import (
    _HORIZONS,
    _was_right,
    run_resolution_tick,
)
from services.sovereign_narrator import (
    _deterministic_fallback,
    narrate_sovereign_decision,
)
from tests.test_sovereign_ai_core import _FakeDB


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── _was_right helper ────────────────────────────────────────────────────────


def test_was_right_long_positive_pnl():
    assert _was_right("LONG", 0.02) is True


def test_was_right_long_negative_pnl_is_wrong():
    assert _was_right("LONG", -0.01) is False


def test_was_right_short_negative_pnl_is_right():
    assert _was_right("SHORT", -0.01) is True


def test_was_right_short_positive_pnl_is_wrong():
    assert _was_right("SHORT", 0.02) is False


def test_was_right_buy_synonym_matches_long():
    assert _was_right("BUY", 0.01) is True


def test_was_right_sell_synonym_matches_short():
    assert _was_right("SELL", -0.01) is True


def test_was_right_hold_is_right_when_move_is_small():
    assert _was_right("HOLD", 0.001) is True
    assert _was_right("HOLD", 0.01) is False


# ─── Resolution loop ──────────────────────────────────────────────────────────


def _seed_decision_with_trade(
    db: _FakeDB, *,
    dec_id: str,
    asset_type: str,
    action: str,
    age: timedelta,
    trade_pnl: float | None = None,
    close_after: timedelta | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    created = now - age
    db["sovereign_decisions"].docs.append({
        "decision_id": dec_id,
        "asset_type": asset_type,
        "symbol": "AAPL" if asset_type == "equity" else "BTCUSDT",
        "action": action,
        "confidence": 0.7,
        "shadow": True,
        "resolved": False,
        "outcomes": {},
        "created_at": created,
    })
    if trade_pnl is not None:
        coll = "paper_trades" if asset_type == "equity" else "crypto_paper_trades"
        db[coll].docs.append({
            "sovereign_decision_id": dec_id,
            "status": "closed",
            "pnl_pct": trade_pnl,
            "closed_at": created + (close_after or timedelta(minutes=75)),
        })


def test_resolution_loop_backpatches_fired_trade_at_60m():
    db = _FakeDB()
    _seed_decision_with_trade(
        db, dec_id="d-1", asset_type="equity", action="LONG",
        age=timedelta(minutes=90), trade_pnl=0.025,
        close_after=timedelta(minutes=65),
    )
    result = _run(run_resolution_tick(db))
    assert result["status"] == "ok"
    assert result["total_resolved"] >= 1
    stored = db["sovereign_decisions"].docs[0]
    assert stored["resolved"] is True
    # 60m horizon was past, so 60m outcome should be stamped
    assert stored["outcomes"]["60m"]["was_right"] is True
    assert stored["outcomes"]["60m"]["pnl_pct"] == 0.025


def test_resolution_loop_skips_decisions_younger_than_horizon():
    db = _FakeDB()
    _seed_decision_with_trade(
        db, dec_id="d-new", asset_type="equity", action="LONG",
        age=timedelta(minutes=15),  # too young for 60m horizon
        trade_pnl=0.02,
    )
    result = _run(run_resolution_tick(db))
    stored = db["sovereign_decisions"].docs[0]
    assert stored["resolved"] is False
    assert stored["outcomes"] == {}
    # Scanned 0 for 60m horizon (age < cutoff)
    per_60m = [t for t in result["per_horizon"] if t["horizon"] == "60m"]
    assert all(t["resolved"] == 0 for t in per_60m)


def test_resolution_loop_skips_no_trade_decisions():
    """Per operator policy: only resolve decisions that have a linked fired trade."""
    db = _FakeDB()
    _seed_decision_with_trade(
        db, dec_id="d-hold", asset_type="equity", action="HOLD",
        age=timedelta(hours=2), trade_pnl=None,  # no trade fired
    )
    result = _run(run_resolution_tick(db))
    stored = db["sovereign_decisions"].docs[0]
    assert stored["resolved"] is False
    # At least one horizon should report skipped_no_trade
    any_skipped = any(
        t["skipped_no_trade"] > 0 for t in result["per_horizon"]
    )
    assert any_skipped


def test_resolution_loop_idempotent():
    db = _FakeDB()
    _seed_decision_with_trade(
        db, dec_id="d-idem", asset_type="equity", action="LONG",
        age=timedelta(hours=5), trade_pnl=0.03,
        close_after=timedelta(minutes=65),
    )
    _run(run_resolution_tick(db))
    stored = db["sovereign_decisions"].docs[0]
    first_outcomes = dict(stored["outcomes"])
    # Second tick — should find no unresolved work for 60m (already stamped)
    result2 = _run(run_resolution_tick(db))
    assert result2["status"] == "ok"
    stored2 = db["sovereign_decisions"].docs[0]
    # 60m outcome should be unchanged
    assert stored2["outcomes"]["60m"] == first_outcomes["60m"]


def test_resolution_loop_handles_crypto_trade():
    db = _FakeDB()
    _seed_decision_with_trade(
        db, dec_id="d-btc", asset_type="crypto", action="SHORT",
        age=timedelta(minutes=90), trade_pnl=-0.015,
        close_after=timedelta(minutes=65),
    )
    result = _run(run_resolution_tick(db))
    assert result["total_resolved"] >= 1
    stored = db["sovereign_decisions"].docs[0]
    # SHORT + negative pnl = sovereign was right
    assert stored["outcomes"]["60m"]["was_right"] is True


def test_resolution_loop_null_db_short_circuits():
    result = _run(run_resolution_tick(None))
    assert result["status"] == "no_db"


# ─── Narrator ────────────────────────────────────────────────────────────────


def test_narrator_deterministic_fallback_returns_three_bullets():
    decision = {
        "symbol": "AAPL",
        "asset_type": "equity",
        "action": "LONG",
        "confidence": 0.72,
        "conviction_tier": "high",
        "size_multiplier": 0.75,
        "vetoes": [],
        "reasons": ["strategist:LONG@0.72"],
        "model_votes": {
            "strategist": {"action": "LONG", "bull": 0.6, "bear": 0.1},
            "regime": {"gate": 1.0, "reason": "regime_ok:trend_up"},
            "catalyst": {"state": "normal", "event_risk": "normal"},
        },
    }
    bullets = _deterministic_fallback(decision)
    assert len(bullets) == 3
    assert "AAPL" in bullets[0]
    assert "LONG" in bullets[0]


def test_narrator_fallback_flags_vetoes():
    decision = {
        "symbol": "AAPL",
        "asset_type": "equity",
        "action": "HOLD",
        "confidence": 0.0,
        "conviction_tier": "low",
        "size_multiplier": 0.0,
        "vetoes": ["LIQUIDITY_TRAP", "NEWS_SHOCK_RESTRICTED"],
        "reasons": [],
        "model_votes": {},
    }
    bullets = _deterministic_fallback(decision)
    assert len(bullets) == 3
    assert any("veto" in b.lower() for b in bullets)


def test_narrator_returns_error_on_missing_decision():
    db = _FakeDB()
    result = _run(narrate_sovereign_decision(db, "nonexistent-id"))
    assert result.get("error") == "decision_not_found"
    assert result["bullets"] == []


def test_narrator_serves_from_cache_on_second_call(monkeypatch):
    # Force fallback path (no LLM key) so test is deterministic
    monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
    db = _FakeDB()
    # Seed a decision
    db["sovereign_decisions"].docs.append({
        "decision_id": "cache-test",
        "symbol": "AAPL",
        "asset_type": "equity",
        "action": "LONG",
        "confidence": 0.70,
        "conviction_tier": "high",
        "size_multiplier": 0.75,
        "vetoes": [],
        "reasons": ["strategist:LONG@0.70"],
        "model_votes": {
            "strategist": {"bull": 0.6, "bear": 0.1},
            "regime": {"gate": 1.0, "reason": "ok"},
            "catalyst": {"state": "normal", "event_risk": "normal"},
        },
    })
    first = _run(narrate_sovereign_decision(db, "cache-test"))
    assert first["cached"] is False
    assert len(first["bullets"]) == 3
    # Second call should hit cache
    second = _run(narrate_sovereign_decision(db, "cache-test"))
    assert second["cached"] is True
    assert second["bullets"] == first["bullets"]


def test_narrator_null_db_returns_error():
    result = _run(narrate_sovereign_decision(None, "x"))
    assert result.get("error") == "no_db"
