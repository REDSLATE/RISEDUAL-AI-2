"""Tests for the USD-notional `execute_signal` helper.

Exercises the 7-step flow end-to-end:
  1. Base size extraction (object + dict configs)
  2. Tier 3 + confidence sizing via `apply_per_trade_sizing`
  3. Low-confidence / risk-filter skip
  4. `MAX_POSITION_USD` hard cap
  5. USD → qty conversion, invalid-price guards
  6. Delegation to `_execute_bot_trade`
  7. Enriched return dict shape
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from services import trading_bot_service as tbs


# ────────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    """These tests exercise the executor BODY, not the lane gate.
    Pin the equity market-hours helper to ``True`` so off-hours
    test runs (e.g. CI on a Sunday) don't hit ``MARKET_CLOSED``
    and miss the body-level assertions. Lane-gate behaviour has
    its own dedicated tests in ``test_executor_lanes.py``."""
    monkeypatch.setattr(
        "services.executors.equity_executor.is_equity_market_open",
        lambda *a, **k: True,
    )


# ────────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def capture_execution(monkeypatch):
    """Patch `_execute_bot_trade` to capture the call args."""
    calls: list[dict] = []

    async def _fake_exec(bot, symbol, side, qty, price, **kw):
        calls.append({
            "bot": bot, "symbol": symbol, "side": side,
            "qty": qty, "price": price, "kw": kw,
        })
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


def _typical_readiness() -> dict:
    """Preview-DB state: score 81.33 with high_conf throttle firing."""
    return {
        "confidence_score": 81.33,
        "unlocked": False,
        "stats": {
            "days": 7, "total_trades": 82, "high_conf_trades": 5,
            "strong_miss_rate": 0.032, "clamp_total": 0,
        },
    }


# ════════════════════════════════════════════════════════════════════════════════
# _extract_trade_size — object + dict config shapes
# ════════════════════════════════════════════════════════════════════════════════

def test_extract_trade_size_from_object():
    cfg = SimpleNamespace(trade_size=1500.0)
    assert tbs._extract_trade_size(cfg) == 1500.0


def test_extract_trade_size_from_dict():
    assert tbs._extract_trade_size({"trade_size": 500}) == 500.0


def test_extract_trade_size_missing_returns_none():
    assert tbs._extract_trade_size(SimpleNamespace()) is None
    assert tbs._extract_trade_size({}) is None
    assert tbs._extract_trade_size(None) is None


def test_extract_trade_size_garbage_returns_none():
    assert tbs._extract_trade_size({"trade_size": "nope"}) is None


# ════════════════════════════════════════════════════════════════════════════════
# execute_signal — happy paths
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_signal_scales_size_and_converts_to_qty(capture_execution):
    """Perfect readiness + 100 confidence → 1.5x mult → $1500 base × 1.5 = $2250,
    capped at MAX_POSITION_USD=$2000. Price $100 → qty 20."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    market_data = {"price": 100}
    config = SimpleNamespace(trade_size=1500, mode="paper", user_id="u1")

    out = await tbs.execute_signal(signal, market_data, _healthy_readiness(), config)

    # Size capped at $2000.
    assert out["size_usd"] == pytest.approx(2000.0, abs=0.01)
    assert out["qty"] == pytest.approx(20.0, abs=0.01)
    assert out["confidence"] == 100
    assert out["readiness_score"] == 100.0
    assert out["base_size"] == 1500.0

    # Order was dispatched.
    assert len(capture_execution) == 1
    call = capture_execution[0]
    assert call["symbol"] == "AAPL"
    assert call["side"] == "BUY"
    assert call["qty"] == pytest.approx(20.0, abs=0.01)


@pytest.mark.asyncio
async def test_execute_signal_scales_down_for_throttled_readiness(capture_execution):
    """2026-05-17 open-trading override: local sizing no longer
    throttles for low readiness — the multiplier is fixed at 1.0
    and only the confidence ramp varies. Confidence 92 → conf_mult
    ≈ 1.308. $1000 × 1.308 ≈ $1308 notional, $1308 / $200 ≈ 6.54
    qty."""
    signal = {"symbol": "MSFT", "entry": 200, "direction": "LONG", "confidence": 92}
    config = SimpleNamespace(trade_size=1000)

    out = await tbs.execute_signal(signal, {}, _typical_readiness(), config)

    # Override: full multiplier × conf ramp ≈ 1.308 → ~$1308 notional.
    assert 1200 < out["size_usd"] < 1400
    assert 6.0 < out["qty"] < 7.0
    assert capture_execution[0]["side"] == "BUY"


@pytest.mark.asyncio
async def test_execute_signal_short_direction_sells(capture_execution):
    signal = {"symbol": "TSLA", "entry": 250, "direction": "SHORT", "confidence": 85}
    config = SimpleNamespace(trade_size=1000)

    await tbs.execute_signal(signal, {}, _healthy_readiness(), config)

    assert capture_execution[0]["side"] == "SELL"


@pytest.mark.asyncio
async def test_execute_signal_uses_market_data_price_when_signal_lacks_entry(
    capture_execution,
):
    signal = {"symbol": "AAPL", "direction": "LONG", "confidence": 90}
    market_data = {"price": 175}
    config = SimpleNamespace(trade_size=500)

    out = await tbs.execute_signal(signal, market_data, _healthy_readiness(), config)
    assert out["qty"] > 0
    # Price must have come from market_data.
    assert capture_execution[0]["price"] == 175


@pytest.mark.asyncio
async def test_execute_signal_accepts_dict_config(capture_execution):
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 90}
    config = {"trade_size": 500, "mode": "paper"}

    out = await tbs.execute_signal(signal, {}, _healthy_readiness(), config)
    assert out["size_usd"] > 0
    assert len(capture_execution) == 1


# ════════════════════════════════════════════════════════════════════════════════
# execute_signal — skip paths
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_signal_skips_below_confidence_gate(capture_execution):
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 40}
    config = SimpleNamespace(trade_size=1000)

    out = await tbs.execute_signal(signal, {}, _healthy_readiness(), config)
    assert out["skipped"] is True
    assert "low confidence" in out["reason"]
    assert capture_execution == []


@pytest.mark.asyncio
async def test_execute_signal_skips_on_invalid_trade_size(capture_execution):
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 90}
    config = SimpleNamespace()  # no trade_size attr

    out = await tbs.execute_signal(signal, {}, _healthy_readiness(), config)
    assert out["skipped"] is True
    assert out["reason"] == "invalid trade_size"
    assert capture_execution == []


@pytest.mark.asyncio
async def test_execute_signal_skips_on_zero_price(capture_execution):
    signal = {"symbol": "AAPL", "direction": "LONG", "confidence": 90}
    config = SimpleNamespace(trade_size=1000)

    out = await tbs.execute_signal(signal, {"price": 0}, _healthy_readiness(), config)
    assert out["skipped"] is True
    assert out["reason"] == "invalid price"
    assert capture_execution == []


@pytest.mark.asyncio
async def test_execute_signal_skips_on_missing_price(capture_execution):
    signal = {"symbol": "AAPL", "direction": "LONG", "confidence": 90}
    config = SimpleNamespace(trade_size=1000)

    out = await tbs.execute_signal(signal, {}, _healthy_readiness(), config)
    assert out["skipped"] is True
    assert out["reason"] == "invalid price"


@pytest.mark.asyncio
async def test_execute_signal_skips_on_tiny_size(capture_execution):
    """Huge price + small trade_size → qty rounds below 1e-6 → skip."""
    signal = {"symbol": "BRK.A", "entry": 500_000, "direction": "LONG", "confidence": 90}
    config = SimpleNamespace(trade_size=0.000001)

    out = await tbs.execute_signal(signal, {}, _healthy_readiness(), config)
    # Either the sizing filter skips OR the qty-too-small skip fires.
    # Both outcomes are acceptable — critical is no trade executes.
    assert out.get("skipped") is True
    assert capture_execution == []


# ════════════════════════════════════════════════════════════════════════════════
# MAX_POSITION_USD hard cap
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_max_position_usd_cap_applied(capture_execution):
    """Base $10000 × 1.5 conf = $15000, must cap at $2000."""
    signal = {"symbol": "AAPL", "entry": 100, "direction": "LONG", "confidence": 100}
    config = SimpleNamespace(trade_size=10_000)

    out = await tbs.execute_signal(signal, {}, _healthy_readiness(), config)
    assert out["size_usd"] == pytest.approx(tbs.MAX_POSITION_USD, abs=0.01)
    assert out["qty"] == pytest.approx(tbs.MAX_POSITION_USD / 100, abs=0.01)


# ════════════════════════════════════════════════════════════════════════════════
# _bot_from_config — delegate selection
# ════════════════════════════════════════════════════════════════════════════════

def test_bot_from_config_prefers_embedded_bot():
    real_bot = {"_id": "real-bot-1", "mode": "live", "user_id": "u42"}
    cfg = SimpleNamespace(trade_size=1000, _bot=real_bot)
    assert tbs._bot_from_config(cfg, "AAPL") is real_bot


def test_bot_from_config_synthesises_paper_stub_when_no_bot():
    cfg = SimpleNamespace(trade_size=1000)
    stub = tbs._bot_from_config(cfg, "AAPL")
    assert stub["mode"] == "paper"
    assert stub["user_id"] is None
    assert "AAPL" in stub["_id"]


def test_bot_from_config_reads_mode_from_dict():
    stub = tbs._bot_from_config({"trade_size": 1000, "mode": "live", "user_id": "u9"}, "AAPL")
    assert stub["mode"] == "live"
    assert stub["user_id"] == "u9"
