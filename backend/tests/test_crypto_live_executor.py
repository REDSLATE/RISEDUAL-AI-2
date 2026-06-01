"""Tripwire coverage for the crypto live executor (Kraken wire).

Doctrine pinned here:
  1. Default OFF — ``RISEDUAL_CRYPTO_LIVE_EXEC=0`` (unset, ``0``,
     ``false``) → ``maybe_route_live`` returns None and caller falls
     back to paper.
  2. LONG only — every SHORT direction must reject with reason
     ``short_blocked``, no Kraken call.
  3. BTC/ETH only — every other symbol rejects with reason
     ``symbol_not_allowlisted``.
  4. Notional ≤ $25 default, env-overridable, clamped to [$5, $1000].
  5. Hard stop-loss at exactly -3% from entry, placed immediately
     after the market buy.
  6. Capacity caps — 3 open / 10 daily are HARD, no env override.
  7. Live writes go to ``crypto_live_trades`` collection ONLY —
     never ``crypto_paper_trades`` (Tier-3 firewall equivalent).
  8. If BUY succeeds but Mongo insert fails → return None so paper
     fires (operator gets the audit twin).
  9. If BUY succeeds but SL placement fails → row still saved but
     ``stop_loss_placed=False`` so the operator can see the orphan.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.crypto_live_executor import (
    HARD_STOP_LOSS_PCT,
    HARD_TAKE_PROFIT_PCT,
    LIVE_SYMBOL_ALLOWLIST,
    MAX_DAILY_LIVE_TRADES,
    MAX_OPEN_LIVE_POSITIONS,
    _resolve_notional_usd,
    is_eligible_for_live,
    kraken_pair,
    maybe_route_live,
    place_live_market_buy,
)


def _trade(**over):
    base = {
        "symbol": "BTC",
        "direction": "LONG",
        "entry_price": 60000.0,
        "size_usd": 25.0,
        "size": 0.000417,
        "confidence": 0.7,
    }
    base.update(over)
    return base


class _FakeColl:
    def __init__(self, open_count=0, daily_count=0):
        self._open = open_count
        self._daily = daily_count
        self.inserted = []
    async def count_documents(self, query):
        if query == {"status": "open"}:
            return self._open
        if "opened_at" in query:
            return self._daily
        return 0
    async def insert_one(self, doc):
        self.inserted.append(doc)
        return MagicMock(inserted_id="fake-id")


class _FakeDB:
    def __init__(self, open_count=0, daily_count=0):
        self.crypto_live_trades = _FakeColl(open_count, daily_count)


# ── Doctrine constants tripwire ────────────────────────────────────────


def test_hard_stop_loss_is_exactly_three_percent():
    """Operator directive 2026-06-01: SL no more than 3%. If anyone
    tries to relax this through a config knob, the test screams."""
    assert HARD_STOP_LOSS_PCT == 0.03


def test_hard_take_profit_is_at_least_four_percent():
    """Operator directive 2026-06-01: TP at 4%+ or greater. The
    constant pins the floor at 4% — making it lower would violate
    the directive. Bumping it up (e.g. 0.05) is allowed by intent."""
    assert HARD_TAKE_PROFIT_PCT >= 0.04


def test_allowlist_is_btc_eth_only():
    assert LIVE_SYMBOL_ALLOWLIST == frozenset({"BTC", "ETH"})


def test_capacity_caps_pinned():
    assert MAX_OPEN_LIVE_POSITIONS == 3
    assert MAX_DAILY_LIVE_TRADES == 10


def test_default_notional_is_25_usd(monkeypatch):
    monkeypatch.delenv("RISEDUAL_CRYPTO_LIVE_NOTIONAL_USD", raising=False)
    assert _resolve_notional_usd() == 25.0


def test_notional_env_override_clamps_to_safe_range(monkeypatch):
    """Defense-in-depth: an operator fat-fingering ``25000`` must
    not push real money. Cap at $1,000, floor at $5."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_NOTIONAL_USD", "25000")
    assert _resolve_notional_usd() == 1000.0
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_NOTIONAL_USD", "1")
    assert _resolve_notional_usd() == 5.0
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_NOTIONAL_USD", "garbage")
    assert _resolve_notional_usd() == 25.0


# ── Symbol mapping ─────────────────────────────────────────────────────


@pytest.mark.parametrize("internal,expected", [
    ("BTC", "XBTUSD"), ("BTC/USD", "XBTUSD"), ("BTC-USD", "XBTUSD"),
    ("ETH", "ETHUSD"), ("ETH/USD", "ETHUSD"), ("ETHUSDT", "ETHUSD"),
    ("btc/usd", "XBTUSD"),
])
def test_kraken_pair_maps_known_symbols(internal, expected):
    assert kraken_pair(internal) == expected


@pytest.mark.parametrize("unsupported", ["SOL", "DOGE", "XRP", "FOO", ""])
def test_kraken_pair_returns_none_for_unsupported(unsupported):
    assert kraken_pair(unsupported) is None


# ── is_eligible_for_live ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eligibility_rejects_when_live_disabled(monkeypatch):
    monkeypatch.delenv("RISEDUAL_CRYPTO_LIVE_EXEC", raising=False)
    db = _FakeDB()
    ok, reason = await is_eligible_for_live(db, "BTC", "LONG")
    assert ok is False
    assert reason == "live_disabled"


@pytest.mark.asyncio
async def test_eligibility_rejects_shorts(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    db = _FakeDB()
    ok, reason = await is_eligible_for_live(db, "BTC", "SHORT")
    assert ok is False
    assert reason == "short_blocked"


@pytest.mark.asyncio
async def test_eligibility_rejects_non_allowlisted_symbol(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    db = _FakeDB()
    ok, reason = await is_eligible_for_live(db, "SOL", "LONG")
    assert ok is False
    assert reason == "symbol_not_allowlisted"


@pytest.mark.asyncio
async def test_eligibility_rejects_when_at_open_cap(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    db = _FakeDB(open_count=MAX_OPEN_LIVE_POSITIONS)
    ok, reason = await is_eligible_for_live(db, "BTC", "LONG")
    assert ok is False
    assert reason == "open_position_cap_reached"


@pytest.mark.asyncio
async def test_eligibility_rejects_when_at_daily_cap(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    db = _FakeDB(daily_count=MAX_DAILY_LIVE_TRADES)
    ok, reason = await is_eligible_for_live(db, "BTC", "LONG")
    assert ok is False
    assert reason == "daily_trade_cap_reached"


@pytest.mark.asyncio
async def test_eligibility_passes_btc_long_under_caps(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    db = _FakeDB(open_count=1, daily_count=2)
    ok, reason = await is_eligible_for_live(db, "BTC", "LONG")
    assert ok is True
    assert reason == "ok"


@pytest.mark.asyncio
async def test_eligibility_accepts_BUY_as_synonym_for_LONG(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    db = _FakeDB()
    ok, _ = await is_eligible_for_live(db, "BTC", "BUY")
    assert ok is True


# ── place_live_market_buy — SL leg ─────────────────────────────────────


def test_buy_places_stop_loss_at_3pct_below_entry(monkeypatch):
    """BUY + SL + TP each go through KrakenTradingService. SL trigger
    price must be exactly entry × 0.97."""
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    fake_client.place_order.side_effect = [
        {"id": "BUY-TXID", "status": "submitted", "symbol": "XBTUSD"},
        {"id": "SL-TXID", "status": "submitted", "symbol": "XBTUSD"},
        {"id": "TP-TXID", "status": "submitted", "symbol": "XBTUSD"},
    ]
    with patch(
        "services.crypto_live_executor._kraken_client",
        return_value=fake_client,
    ):
        fill = place_live_market_buy("BTC", notional_usd=25.0, ref_price=60000.0)
    assert fill is not None
    assert fill["kraken_order_id"] == "BUY-TXID"
    assert fill["stop_loss_placed"] is True
    assert fill["stop_loss_order_id"] == "SL-TXID"
    # SL = 60000 × 0.97 = 58200.00
    assert fill["stop_loss_price"] == 58200.0
    # Second call to place_order must be the stop-loss SELL.
    sl_call = fake_client.place_order.call_args_list[1]
    assert sl_call.kwargs["side"] == "sell"
    assert sl_call.kwargs["order_type"] == "stop-loss"
    assert sl_call.kwargs["stop_price"] == 58200.0


def test_buy_places_take_profit_at_4pct_above_entry(monkeypatch):
    """TP leg must be a LIMIT SELL at exactly entry × 1.04."""
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    fake_client.place_order.side_effect = [
        {"id": "BUY-TXID", "status": "submitted", "symbol": "XBTUSD"},
        {"id": "SL-TXID", "status": "submitted", "symbol": "XBTUSD"},
        {"id": "TP-TXID", "status": "submitted", "symbol": "XBTUSD"},
    ]
    with patch(
        "services.crypto_live_executor._kraken_client",
        return_value=fake_client,
    ):
        fill = place_live_market_buy("BTC", notional_usd=25.0, ref_price=60000.0)
    assert fill is not None
    assert fill["take_profit_placed"] is True
    assert fill["take_profit_order_id"] == "TP-TXID"
    # TP = 60000 × 1.04 = 62400.00
    assert fill["take_profit_price"] == 62400.0
    # Third call to place_order must be the limit SELL at TP.
    tp_call = fake_client.place_order.call_args_list[2]
    assert tp_call.kwargs["side"] == "sell"
    assert tp_call.kwargs["order_type"] == "limit"
    assert tp_call.kwargs["limit_price"] == 62400.0


def test_buy_sl_succeeds_tp_fails_returns_both_markers(monkeypatch):
    """SL is the critical leg (downside). TP failure must NOT undo
    a successful SL placement — the row still ships with
    ``stop_loss_placed=True`` and ``take_profit_placed=False``."""
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    fake_client.place_order.side_effect = [
        {"id": "BUY-TXID", "status": "submitted", "symbol": "XBTUSD"},
        {"id": "SL-TXID", "status": "submitted", "symbol": "XBTUSD"},
        Exception("TP service unavailable"),
    ]
    with patch(
        "services.crypto_live_executor._kraken_client",
        return_value=fake_client,
    ):
        fill = place_live_market_buy("BTC", notional_usd=25.0, ref_price=60000.0)
    assert fill is not None
    assert fill["stop_loss_placed"] is True
    assert fill["take_profit_placed"] is False


def test_buy_succeeds_but_sl_fails_returns_orphan_marker(monkeypatch):
    """If SL placement raises or returns empty, the fill dict still
    comes back but ``stop_loss_placed=False`` so the operator can
    see the open position has no broker-side stop. TP attempt still
    fires (it's independent of SL)."""
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    fake_client.place_order.side_effect = [
        {"id": "BUY-TXID", "status": "submitted", "symbol": "XBTUSD"},
        Exception("SL service unavailable"),
        {"id": "TP-TXID", "status": "submitted", "symbol": "XBTUSD"},
    ]
    with patch(
        "services.crypto_live_executor._kraken_client",
        return_value=fake_client,
    ):
        fill = place_live_market_buy("BTC", notional_usd=25.0, ref_price=60000.0)
    assert fill is not None
    assert fill["kraken_order_id"] == "BUY-TXID"  # BUY succeeded
    assert fill["stop_loss_placed"] is False     # SL did not
    assert fill["stop_loss_order_id"] == ""
    assert fill["take_profit_placed"] is True    # TP fired independently


def test_buy_failure_returns_none_no_sl_attempted(monkeypatch):
    """If the BUY itself fails, NO SL leg should fire — there's no
    position to protect."""
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    fake_client = MagicMock()
    fake_client.place_order.return_value = None
    with patch(
        "services.crypto_live_executor._kraken_client",
        return_value=fake_client,
    ):
        fill = place_live_market_buy("BTC", notional_usd=25.0, ref_price=60000.0)
    assert fill is None
    assert fake_client.place_order.call_count == 1


def test_buy_rejects_zero_ref_price():
    assert place_live_market_buy("BTC", 25.0, 0.0) is None
    assert place_live_market_buy("BTC", 25.0, -1.0) is None


# ── maybe_route_live integration ───────────────────────────────────────


@pytest.mark.asyncio
async def test_maybe_route_live_returns_none_when_disabled(monkeypatch):
    """Default behavior — paper path continues unchanged."""
    monkeypatch.delenv("RISEDUAL_CRYPTO_LIVE_EXEC", raising=False)
    db = _FakeDB()
    out = await maybe_route_live(db, _trade())
    assert out is None
    assert db.crypto_live_trades.inserted == []


@pytest.mark.asyncio
async def test_maybe_route_live_rejects_short(monkeypatch):
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    db = _FakeDB()
    out = await maybe_route_live(db, _trade(direction="SHORT"))
    assert out is None
    assert db.crypto_live_trades.inserted == []


@pytest.mark.asyncio
async def test_maybe_route_live_persists_to_live_collection_only(monkeypatch):
    """Tier-3 firewall equivalent: live row goes to
    ``crypto_live_trades`` ONLY — never ``crypto_paper_trades``."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")
    db = _FakeDB()

    fake_fill = {
        "kraken_order_id": "BUY-1",
        "kraken_pair": "XBTUSD",
        "qty": 0.000417,
        "notional_usd": 25.0,
        "stop_loss_price": 58200.0,
        "stop_loss_order_id": "SL-1",
        "stop_loss_placed": True,
        "take_profit_price": 62400.0,
        "take_profit_order_id": "TP-1",
        "take_profit_placed": True,
    }
    with patch(
        "services.crypto_live_executor.place_live_market_buy",
        return_value=fake_fill,
    ):
        out = await maybe_route_live(db, _trade())
    assert out is not None
    assert out["is_live"] is True
    assert out["kraken_order_id"] == "BUY-1"
    assert out["stop_loss_placed"] is True
    assert out["stop_loss_pct"] == 0.03
    assert out["take_profit_placed"] is True
    assert out["take_profit_pct"] == 0.04
    assert out["take_profit_price"] == 62400.0
    # Only one collection touched — the live one.
    assert len(db.crypto_live_trades.inserted) == 1
    assert not hasattr(db, "crypto_paper_trades") or \
        not getattr(db, "crypto_paper_trades", None) or \
        (hasattr(getattr(db, "crypto_paper_trades", None), "inserted") and
         db.crypto_paper_trades.inserted == [])  # noqa: B015


@pytest.mark.asyncio
async def test_maybe_route_live_carries_safety_caps_snapshot(monkeypatch):
    """The persisted live row must snapshot the safety caps that
    were in effect at fill time, so an operator looking at a row
    months later can audit "what was the cap when this filled?""."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")
    db = _FakeDB()
    with patch(
        "services.crypto_live_executor.place_live_market_buy",
        return_value={
            "kraken_order_id": "X", "kraken_pair": "XBTUSD", "qty": 0.0004,
            "notional_usd": 25.0, "stop_loss_price": 58200.0,
            "stop_loss_order_id": "Y", "stop_loss_placed": True,
            "take_profit_price": 62400.0,
            "take_profit_order_id": "Z", "take_profit_placed": True,
        },
    ):
        out = await maybe_route_live(db, _trade())
    caps = out["live_safety_caps"]
    assert caps["stop_loss_pct"] == 0.03
    assert caps["take_profit_pct"] == 0.04
    assert caps["notional_cap_usd"] == 25.0
    assert caps["open_position_cap"] == 3
    assert caps["daily_trade_cap"] == 10
    assert caps["symbol_allowlist"] == ["BTC", "ETH"]


@pytest.mark.asyncio
async def test_maybe_route_live_returns_none_when_kraken_rejects(monkeypatch):
    """Kraken rejection → caller falls back to paper path. We must
    NOT silently swallow + write a phantom row."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")
    db = _FakeDB()
    with patch(
        "services.crypto_live_executor.place_live_market_buy",
        return_value=None,
    ):
        out = await maybe_route_live(db, _trade())
    assert out is None
    assert db.crypto_live_trades.inserted == []


@pytest.mark.asyncio
async def test_maybe_route_live_returns_none_when_db_insert_fails(monkeypatch):
    """BUY succeeded but Mongo insert blew up. We MUST return None
    so the caller's paper path also fires — the operator can use the
    paper twin to reconcile the orphan Kraken position manually."""
    monkeypatch.setenv("RISEDUAL_CRYPTO_LIVE_EXEC", "1")
    monkeypatch.setenv("KRAKEN_API_KEY", "fake")
    monkeypatch.setenv("KRAKEN_API_SECRET", "fake")

    class _BoomColl:
        async def count_documents(self, _q): return 0
        async def insert_one(self, _d): raise RuntimeError("db down")

    class _BoomDB:
        crypto_live_trades = _BoomColl()

    with patch(
        "services.crypto_live_executor.place_live_market_buy",
        return_value={
            "kraken_order_id": "X", "kraken_pair": "XBTUSD", "qty": 0.0004,
            "notional_usd": 25.0, "stop_loss_price": 58200.0,
            "stop_loss_order_id": "Y", "stop_loss_placed": True,
            "take_profit_price": 62400.0,
            "take_profit_order_id": "Z", "take_profit_placed": True,
        },
    ):
        out = await maybe_route_live(_BoomDB(), _trade())
    assert out is None
