"""Regression tests for services.terminal_aggregator.

Two surfaces under test:
    * get_market_state — stale / closed / stressed / active paths
    * get_signal       — conviction tier surfacing, sizing anchor,
                          why/risks composition, empty-collection safety

The "no HOLD / correlation-aware" ranker tests are deliberately NOT
here — ``/top-actions`` is Phase T2 and hasn't shipped yet. The spec
from the user uses a sync mongo surface in examples; this codebase is
async-Motor, so we adapt: the public functions are awaitable and the
fake db exposes async ``find_one``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# Reuse the Motor-compatible fake collection from existing tests. It
# already implements async find_one/insert_one and honours _matches
# semantics including range operators.
from tests.test_top_universe_service import _FakeDB  # noqa: E402


def _fixed_now() -> datetime:
    return datetime(2026, 5, 4, 15, 0, tzinfo=timezone.utc)


def _mature_row(symbol: str = "NVDA", *, mature: bool = True,
                avg_bps: float = 34.0, p90_bps: float = 48.0,
                flow_imbalance: float = 0.31, pcr: float = 0.72,
                mean_iv: float = 0.41, total_volume: int = 843_221,
                stable_minutes: int = 18) -> dict:
    """Builds a per-symbol row matching the real warm-writer output."""
    return {
        "symbol": symbol,
        "has_hot_flow": True,
        "flow_maturity": mature,
        "stable_minutes": stable_minutes,
        "contracts": [],
        "aggregate": {
            "put_call_ratio": pcr,
            "total_volume": total_volume,
            "mean_iv": mean_iv,
            "avg_spread_bps": avg_bps,
            "p90_spread_bps": p90_bps,
            "flow_imbalance": flow_imbalance,
        },
    }


# ═══════════════ get_market_state ═══════════════


@pytest.mark.asyncio
async def test_market_state_stale_snapshot_returns_closed_no_signal():
    """A snapshot older than 10 min must flip market_state to CLOSED and
    zero the counts — the staleness guard is the "additive, never
    dominant" rule applied at the terminal layer."""
    from services.terminal_aggregator import (
        TerminalContext, get_market_state,
    )

    now = _fixed_now()
    db = _FakeDB()
    db["option_universe"].docs.append({
        "_id": "current",
        "updated_at": (now - timedelta(minutes=20)).isoformat(),
        "is_market_open": True,
        "data": [_mature_row()],
    })

    out = await get_market_state(TerminalContext(db=db, now=now))

    assert out["snapshot_fresh"] is False
    assert out["market_state"] == "CLOSED"
    assert out["tradeable_symbols"] == 0
    assert out["tracked_symbols"] == 0
    assert out["liquidity_stress"]["state"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_market_state_active_with_normal_liquidity():
    from services.terminal_aggregator import (
        TerminalContext, get_market_state,
    )

    now = _fixed_now()
    db = _FakeDB()
    db["option_universe"].docs.append({
        "_id": "current",
        "updated_at": now.isoformat(),
        "is_market_open": True,
        "data": [
            _mature_row("SPY", avg_bps=30.0, p90_bps=48.0),
            _mature_row("QQQ", avg_bps=28.0, p90_bps=52.0),
            _mature_row("IWM", mature=False, avg_bps=40.0, p90_bps=70.0),
        ],
    })

    out = await get_market_state(TerminalContext(db=db, now=now))

    assert out["snapshot_fresh"] is True
    assert out["market_state"] == "ACTIVE"
    # Only SPY + QQQ are mature + healthy stress → tradeable
    assert out["tradeable_symbols"] == 2
    assert out["tracked_symbols"] == 3
    assert out["liquidity_stress"]["state"] in ("NORMAL", "CAUTION")
    assert out["session_phase"] in ("OPENING", "MIDDAY", "CLOSING", "ACTIVE")


@pytest.mark.asyncio
async def test_market_state_flags_stressed_when_avg_stress_high():
    """Chain-wide stress ratio ≥ 4 → market_state STRESSED even if
    individual symbols report mature flow. This is the aggregate
    defensive cut — one hot symbol doesn't rescue a fragile market."""
    from services.terminal_aggregator import (
        TerminalContext, get_market_state,
    )

    now = _fixed_now()
    db = _FakeDB()
    db["option_universe"].docs.append({
        "_id": "current",
        "updated_at": now.isoformat(),
        "is_market_open": True,
        "data": [
            _mature_row("SPY", avg_bps=50.0, p90_bps=250.0),   # stress ~5
            _mature_row("QQQ", avg_bps=50.0, p90_bps=260.0),   # stress ~5.2
        ],
    })

    out = await get_market_state(TerminalContext(db=db, now=now))

    assert out["market_state"] == "STRESSED"
    assert out["liquidity_stress"]["state"] == "STRESSED"
    # Stressed symbols are NOT counted as tradeable, even though mature
    assert out["tradeable_symbols"] == 0


@pytest.mark.asyncio
async def test_market_state_closed_when_market_flag_false():
    from services.terminal_aggregator import (
        TerminalContext, get_market_state,
    )

    now = _fixed_now()
    db = _FakeDB()
    db["option_universe"].docs.append({
        "_id": "current",
        "updated_at": now.isoformat(),
        "is_market_open": False,
        "data": [_mature_row()],
    })

    out = await get_market_state(TerminalContext(db=db, now=now))

    assert out["market_state"] == "CLOSED"
    assert out["session_phase"] == "CLOSED"


@pytest.mark.asyncio
async def test_market_state_missing_snapshot_entirely():
    """No doc at all must fail safe → CLOSED, no throws."""
    from services.terminal_aggregator import (
        TerminalContext, get_market_state,
    )
    out = await get_market_state(TerminalContext(db=_FakeDB(), now=_fixed_now()))
    assert out["market_state"] == "CLOSED"
    assert out["snapshot_fresh"] is False


# ═══════════════ get_signal ═══════════════


@pytest.mark.asyncio
async def test_signal_uses_conviction_tier_not_raw_probability_headline():
    """Critical product rule: raw prob + tier both surface, but tier is
    the anchor. Calibration context carries the sample/hit-rate info
    that gives the raw number meaning."""
    from services.terminal_aggregator import TerminalContext, get_signal

    now = _fixed_now()
    db = _FakeDB()
    db["latest_signal_snapshots"].docs.append({
        "symbol": "NVDA",
        "action": "BUY",
        "conviction_score": 0.78,
        "conviction_tier": "STRONG",
        "conviction_breakdown": {"regime_match": 0.2, "signal_confidence": 0.35},
    })
    db["conviction_calibration"].docs.append({
        "tier": "STRONG",
        "hit_rate": 0.71,
        "sample_size": 100,
        "window_days": 90,
    })

    out = await get_signal(TerminalContext(db=db, now=now), "NVDA")

    assert out["conviction"]["tier"] == "STRONG"
    assert out["conviction"]["score"] == 0.78
    assert out["conviction"]["calibration_context"]["hit_rate"] == 0.71
    assert out["conviction"]["calibration_context"]["sample_size"] == 100


@pytest.mark.asyncio
async def test_signal_sizing_carries_risk_budget_explainer():
    """Sizing must never be surfaced as naked percent — the explainer
    string is mandatory so the UI can't accidentally omit the anchor."""
    from services.terminal_aggregator import TerminalContext, get_signal

    db = _FakeDB()
    db["latest_signal_snapshots"].docs.append({
        "symbol": "NVDA",
        "action": "BUY",
        "suggested_size": {"risk_budget_pct": 50, "estimated_dollars": 2500},
    })

    out = await get_signal(TerminalContext(db=db, now=_fixed_now()), "NVDA")

    assert out["sizing"]["basis"] == "risk_budget"
    assert out["sizing"]["risk_budget_pct"] == 50
    assert out["sizing"]["estimated_dollars"] == 2500
    assert "not percent of total account" in out["sizing"]["explainer"]


@pytest.mark.asyncio
async def test_signal_why_sorted_by_absolute_contribution_magnitude():
    """The biggest mover is always first regardless of sign — users
    should see what matters most, positive or negative."""
    from services.terminal_aggregator import TerminalContext, get_signal

    db = _FakeDB()
    db["latest_signal_snapshots"].docs.append({
        "symbol": "NVDA",
        "conviction_breakdown": {
            "signal_confidence": 0.08,
            "regime_match": -0.22,     # biggest by abs — must be first
            "options_flow_boost": 0.15,
            "calibration": 0.0,         # zero dropped
        },
    })

    out = await get_signal(TerminalContext(db=db, now=_fixed_now()), "NVDA")

    why = out["why"]
    assert len(why) == 3  # zero-contrib 'calibration' dropped
    assert why[0]["factor"] == "regime_match"
    assert why[0]["impact"] == "negative"
    assert why[1]["factor"] == "options_flow_boost"
    assert why[2]["factor"] == "signal_confidence"
    # All rows have human-readable labels
    assert all(row["label"] for row in why)


@pytest.mark.asyncio
async def test_signal_risks_composed_from_three_sources():
    """Signal flags + options liquidity stress + portfolio correlation
    all land in the same ``risks`` array with a ``source`` tag so the
    UI can group or filter by origin."""
    from services.terminal_aggregator import TerminalContext, get_signal

    now = _fixed_now()
    db = _FakeDB()
    db["option_universe"].docs.append({
        "_id": "current",
        "updated_at": now.isoformat(),
        "is_market_open": True,
        "data": [_mature_row("NVDA", avg_bps=50.0, p90_bps=260.0)],  # stress ~5.2
    })
    db["latest_signal_snapshots"].docs.append({
        "symbol": "NVDA",
        "action": "BUY",
        "risk_flags": ["IV_ELEVATED"],
    })
    db["position_context"].docs.append({
        "user_id": "owner",
        "symbol": "NVDA",
        "already_owned": True,
        "correlation_flag": "HIGH",
    })

    out = await get_signal(
        TerminalContext(db=db, now=now), "NVDA", user_id="owner",
    )

    sources = {r["source"] for r in out["risks"]}
    assert "signal" in sources             # IV_ELEVATED
    assert "options_liquidity" in sources  # stressed
    assert "portfolio" in sources          # correlation + already-owned


@pytest.mark.asyncio
async def test_signal_tradeability_stressed_market_is_not_tradeable():
    """Even a mature, hot-flow symbol is NOT tradeable when the
    spread-distribution stress ratio is in the STRESSED band. The
    tradeability verdict is the single UI badge — must reflect the
    conservative answer."""
    from services.terminal_aggregator import TerminalContext, get_signal

    now = _fixed_now()
    db = _FakeDB()
    db["option_universe"].docs.append({
        "_id": "current",
        "updated_at": now.isoformat(),
        "is_market_open": True,
        "data": [_mature_row("NVDA", avg_bps=50.0, p90_bps=260.0)],
    })
    db["latest_signal_snapshots"].docs.append(
        {"symbol": "NVDA", "action": "BUY"},
    )

    out = await get_signal(TerminalContext(db=db, now=now), "NVDA")

    assert out["tradeability"]["state"] == "NOT_TRADEABLE"
    assert out["tradeability"]["maturity"] == "STRESSED"


@pytest.mark.asyncio
async def test_signal_degrades_gracefully_with_empty_collections():
    """No signal snapshots, no calibration, no options data, no position
    context → response still 200-ish-shaped, all sections safe-defaulted.
    Critical for the pre-seeding window."""
    from services.terminal_aggregator import TerminalContext, get_signal

    out = await get_signal(TerminalContext(db=_FakeDB(), now=_fixed_now()), "NVDA")

    assert out["symbol"] == "NVDA"
    assert out["action"] == "UNKNOWN"
    assert out["conviction"]["tier"] == "WEAK"
    assert out["why"] == []
    assert out["risks"] == []
    assert out["tradeability"]["state"] == "NOT_TRADEABLE"
    assert out["position_context"] is None


@pytest.mark.asyncio
async def test_signal_position_context_only_fetched_when_user_id_passed():
    """Symbol detail with no user_id → position_context is None (no
    per-user lookup runs). Keeps the market-agnostic view clean."""
    from services.terminal_aggregator import TerminalContext, get_signal

    db = _FakeDB()
    db["position_context"].docs.append({
        "user_id": "owner", "symbol": "NVDA",
        "already_owned": True, "correlation_flag": "HIGH",
    })
    db["latest_signal_snapshots"].docs.append(
        {"symbol": "NVDA", "action": "BUY"},
    )

    out_anon = await get_signal(
        TerminalContext(db=db, now=_fixed_now()), "NVDA",
    )
    out_user = await get_signal(
        TerminalContext(db=db, now=_fixed_now()), "NVDA", user_id="owner",
    )

    assert out_anon["position_context"] is None
    assert out_user["position_context"]["correlation_flag"] == "HIGH"


@pytest.mark.asyncio
async def test_signal_symbol_normalized_to_upper():
    from services.terminal_aggregator import TerminalContext, get_signal
    out = await get_signal(
        TerminalContext(db=_FakeDB(), now=_fixed_now()), "nvda",
    )
    assert out["symbol"] == "NVDA"
