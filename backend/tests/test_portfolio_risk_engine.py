"""Tests for the Portfolio Risk Engine.

Exercises the three building blocks:
  * :func:`get_total_exposure` — sums `size_usd` across open positions.
  * :func:`get_open_trade_count` — concurrency counter.
  * :func:`apply_portfolio_constraints` — shrink-or-zero gate.

And the opt-in integration path through :func:`execute_signal`
(``open_positions`` kwarg). When the portfolio saturates either cap,
``execute_signal`` must skip with ``reason="portfolio limits reached"``
and no broker order must fire.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import trading_bot_service as tbs


# ────────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def capture_execution(monkeypatch):
    """Patch `_execute_bot_trade` to capture the call args."""
    calls: list[dict] = []

    async def _fake_exec(bot, symbol, side, qty, price, **kw):
        calls.append({"symbol": symbol, "side": side, "qty": qty, "price": price})
        return {"status": "filled", "filled_qty": qty, "filled_price": price}

    monkeypatch.setattr(tbs, "_execute_bot_trade", _fake_exec)
    return calls


def _healthy_readiness() -> dict:
    return {
        "confidence_score": 100.0,
        "unlocked": True,
        "stats": {
            "days": 30, "total_trades": 150, "high_conf_trades": 60,
            "strong_miss_rate": 0.05, "clamp_total": 0,
        },
    }


# ════════════════════════════════════════════════════════════════════════════════
# get_total_exposure
# ════════════════════════════════════════════════════════════════════════════════

def test_total_exposure_empty_list_returns_zero():
    assert tbs.get_total_exposure([]) == 0.0


def test_total_exposure_none_returns_zero():
    assert tbs.get_total_exposure(None) == 0.0


def test_total_exposure_sums_size_usd():
    positions = [
        {"size_usd": 500},
        {"size_usd": 1000.5},
        {"size_usd": 250.25},
    ]
    assert tbs.get_total_exposure(positions) == pytest.approx(1750.75)


def test_total_exposure_tolerates_missing_keys():
    positions = [{"size_usd": 500}, {}, {"size_usd": 100}]
    assert tbs.get_total_exposure(positions) == 600.0


def test_total_exposure_tolerates_garbage_values():
    positions = [
        {"size_usd": "not-a-number"},
        {"size_usd": None},
        {"size_usd": 300},
    ]
    assert tbs.get_total_exposure(positions) == 300.0


# ════════════════════════════════════════════════════════════════════════════════
# get_open_trade_count
# ════════════════════════════════════════════════════════════════════════════════

def test_open_trade_count_empty():
    assert tbs.get_open_trade_count([]) == 0
    assert tbs.get_open_trade_count(None) == 0


def test_open_trade_count_matches_len():
    assert tbs.get_open_trade_count([{}, {}, {}]) == 3


# ════════════════════════════════════════════════════════════════════════════════
# apply_portfolio_constraints
# ════════════════════════════════════════════════════════════════════════════════

def test_portfolio_constraints_no_positions_passes_through():
    """Empty portfolio → full headroom → proposed size returned unchanged."""
    assert tbs.apply_portfolio_constraints(1000.0, []) == 1000.0


def test_portfolio_constraints_within_caps_passes_through():
    positions = [{"size_usd": 500}, {"size_usd": 500}]  # $1000 used, 2 trades
    # remaining = 3000 - 1000 = 2000, proposed 800 < 2000 → unchanged
    assert tbs.apply_portfolio_constraints(800.0, positions) == 800.0


def test_portfolio_constraints_shrinks_to_remaining_headroom():
    """Proposed size exceeds remaining exposure → shrink to remaining."""
    positions = [{"size_usd": 2000}]  # $2000 used → $1000 headroom
    assert tbs.apply_portfolio_constraints(1500.0, positions) == 1000.0


def test_portfolio_constraints_zero_when_exposure_saturated():
    """Exposure at cap → no headroom → returns 0."""
    positions = [{"size_usd": tbs.MAX_PORTFOLIO_EXPOSURE}]
    assert tbs.apply_portfolio_constraints(500.0, positions) == 0.0


def test_portfolio_constraints_zero_when_exposure_over_cap():
    """Exposure exceeds cap (shouldn't happen but be defensive) → 0."""
    positions = [{"size_usd": 5000}]
    assert tbs.apply_portfolio_constraints(500.0, positions) == 0.0


def test_portfolio_constraints_zero_when_concurrency_saturated():
    """5 open trades → MAX_CONCURRENT_TRADES hit → returns 0 even with headroom."""
    positions = [{"size_usd": 100}] * tbs.MAX_CONCURRENT_TRADES
    assert tbs.apply_portfolio_constraints(500.0, positions) == 0.0


def test_portfolio_constraints_concurrency_cap_exact_boundary():
    """4 open trades (one under cap) → new trade permitted."""
    positions = [{"size_usd": 100}] * (tbs.MAX_CONCURRENT_TRADES - 1)
    out = tbs.apply_portfolio_constraints(500.0, positions)
    assert out == 500.0


def test_portfolio_constraints_concurrency_trumps_exposure_check():
    """Concurrency cap is checked first — returns 0 even when exposure is 0."""
    # 5 zero-size trades is pathological but tests ordering.
    positions = [{"size_usd": 0}] * tbs.MAX_CONCURRENT_TRADES
    assert tbs.apply_portfolio_constraints(500.0, positions) == 0.0


# ════════════════════════════════════════════════════════════════════════════════
# execute_signal — portfolio-constraint integration
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_signal_no_open_positions_kwarg_skips_portfolio_check(
    capture_execution,
):
    """Omitting `open_positions` (or passing None) disables portfolio gating."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1500)

    out = await tbs.execute_signal(signal, {}, _healthy_readiness(), config)
    assert out.get("skipped") is not True
    assert len(capture_execution) == 1


@pytest.mark.asyncio
async def test_execute_signal_empty_open_positions_still_executes(capture_execution):
    """Empty list = 0 exposure, 0 trades → full headroom → trade fires."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1500)

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config, open_positions=[]
    )
    assert out.get("skipped") is not True
    assert len(capture_execution) == 1


@pytest.mark.asyncio
async def test_execute_signal_shrinks_to_portfolio_headroom(capture_execution):
    """Base $1500, readiness cap $2000, but only $500 headroom left → final $500."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1500)
    open_positions = [{"size_usd": 2500}]  # remaining = 3000 - 2500 = 500

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config, open_positions=open_positions
    )
    assert out.get("skipped") is not True
    assert out["size_usd"] == pytest.approx(500.0, abs=0.01)
    assert out["qty"] == pytest.approx(5.0, abs=0.01)
    assert len(capture_execution) == 1


@pytest.mark.asyncio
async def test_execute_signal_skips_when_portfolio_exposure_saturated(
    capture_execution,
):
    """Exposure at MAX_PORTFOLIO_EXPOSURE → skip with portfolio reason."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1000)
    open_positions = [{"size_usd": tbs.MAX_PORTFOLIO_EXPOSURE}]

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config, open_positions=open_positions
    )
    assert out["skipped"] is True
    assert out["reason"] == "portfolio limits reached"
    assert capture_execution == []


@pytest.mark.asyncio
async def test_execute_signal_skips_when_concurrency_saturated(capture_execution):
    """5 open positions (even if small) → skip with portfolio reason."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=1000)
    open_positions = [{"size_usd": 100}] * tbs.MAX_CONCURRENT_TRADES

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config, open_positions=open_positions
    )
    assert out["skipped"] is True
    assert out["reason"] == "portfolio limits reached"
    assert capture_execution == []


@pytest.mark.asyncio
async def test_execute_signal_portfolio_cap_trumps_max_position_usd(capture_execution):
    """Without portfolio check the trade would hit the $2000 per-trade cap.
    With portfolio at $2500, headroom is only $500 — the portfolio cap wins."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=10_000)  # would be capped at $2000
    open_positions = [{"size_usd": 2500}]  # only $500 headroom

    out = await tbs.execute_signal(
        signal, {}, _healthy_readiness(), config, open_positions=open_positions
    )
    assert out["size_usd"] == pytest.approx(500.0, abs=0.01)
    assert len(capture_execution) == 1
