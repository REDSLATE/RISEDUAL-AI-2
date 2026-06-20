"""Public.com equity live executor — tripwire suite (2026-06-12).

Operator directive: drop Alpaca, route equity intents to Public.com.

Doctrine pins in this file:
    1. Default OFF — no env knob → ``maybe_route_live`` is a no-op.
    2. LONG-only — SELL/SHORT directions skip silently.
    3. Allowlist enforcement when ``PUBLIC_LIVE_SYMBOLS`` is set.
    4. Connect-state gate — refuse to fire without an active
       ``broker_connections`` row for broker_id="public".
    5. Idempotency — refuse to open a 2nd live row for the same
       open symbol.
    6. Notional cap clamped to [1, 1000].
    7. Quote-probe failure → skip, no order placed.
    8. Place-order returning None (auth fail) → no Mongo write.
    9. Place-order success path inserts the row with full provenance.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.public_equity_live_executor import (
    _allowed_symbols,
    _fixed_notional_usd,
    _live_exec_enabled,
    maybe_route_live,
)


# ── Env contract ─────────────────────────────────────────────────────


def test_live_exec_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RISEDUAL_PUBLIC_LIVE_EXEC", raising=False)
    assert _live_exec_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "True", "yes", "on"])
def test_live_exec_enabled_truthy(monkeypatch, val):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", val)
    assert _live_exec_enabled() is True


def test_fixed_notional_default(monkeypatch):
    monkeypatch.delenv("PUBLIC_LIVE_NOTIONAL_USD", raising=False)
    assert _fixed_notional_usd() == 25.0


@pytest.mark.parametrize("raw,expected", [
    ("10", 10.0),
    ("100", 100.0),
    ("0.5", 1.0),       # clamped up
    ("999999", 1000.0), # clamped down
    ("garbage", 25.0),  # fallback
])
def test_fixed_notional_bounds(monkeypatch, raw, expected):
    monkeypatch.setenv("PUBLIC_LIVE_NOTIONAL_USD", raw)
    assert _fixed_notional_usd() == expected


def test_allowed_symbols_unset(monkeypatch):
    monkeypatch.delenv("PUBLIC_LIVE_SYMBOLS", raising=False)
    assert _allowed_symbols() is None


def test_allowed_symbols_parses_csv(monkeypatch):
    monkeypatch.setenv("PUBLIC_LIVE_SYMBOLS", "aapl, msft ,nvda")
    assert _allowed_symbols() == {"AAPL", "MSFT", "NVDA"}


# ── Fakes ────────────────────────────────────────────────────────────


class _FakeColl:
    def __init__(self):
        self.docs: list[dict] = []
        self._find_one_response = None

    async def find_one(self, _filter, _proj=None):
        return self._find_one_response

    async def insert_one(self, doc):
        self.docs.append(doc)
        return MagicMock(inserted_id="fake")


class _FakeDB:
    def __init__(self):
        self.broker_connections = _FakeColl()
        self.equity_live_trades = _FakeColl()

    def __getitem__(self, k):
        return getattr(self, k)


# ── Gate behavior ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_short_circuits(monkeypatch):
    monkeypatch.delenv("RISEDUAL_PUBLIC_LIVE_EXEC", raising=False)
    db = _FakeDB()
    out = await maybe_route_live(db, intent={"symbol": "AAPL", "direction": "BUY"})
    assert out is None
    assert db.equity_live_trades.docs == []


@pytest.mark.asyncio
async def test_skips_non_buy_direction(monkeypatch):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    db = _FakeDB()
    out = await maybe_route_live(db, intent={"symbol": "AAPL", "direction": "SELL"})
    assert out is None


@pytest.mark.asyncio
async def test_blocked_by_allowlist(monkeypatch):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.setenv("PUBLIC_LIVE_SYMBOLS", "MSFT,NVDA")
    db = _FakeDB()
    out = await maybe_route_live(db, intent={"symbol": "AAPL", "direction": "BUY"})
    assert out is None


@pytest.mark.asyncio
async def test_no_connect_skips(monkeypatch):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = None  # no connect
    out = await maybe_route_live(db, intent={"symbol": "AAPL", "direction": "BUY"})
    assert out is None


@pytest.mark.asyncio
async def test_idempotency_refuses_dup(monkeypatch):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "secret", "api_secret": "acct",
    }
    db.equity_live_trades._find_one_response = {"_id": "existing-open-row"}
    out = await maybe_route_live(db, intent={"symbol": "AAPL", "direction": "BUY"})
    assert out is None


@pytest.mark.asyncio
async def test_quote_failure_skips(monkeypatch):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "secret", "api_secret": "acct",
    }
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=None),
    ):
        out = await maybe_route_live(db, intent={"symbol": "AAPL", "direction": "BUY"})
    assert out is None


@pytest.mark.asyncio
async def test_place_order_failure_no_mongo_write(monkeypatch):
    """Public.com auth/order failure must NOT leave a row claiming
    a fill we don't have."""
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "secret", "api_secret": "acct",
    }
    fake_client = MagicMock()
    fake_client.place_order.return_value = None  # auth failed
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=fake_client,
    ):
        out = await maybe_route_live(db, intent={"symbol": "AAPL", "direction": "BUY"})
    assert out is None
    assert db.equity_live_trades.docs == []


@pytest.mark.asyncio
async def test_happy_path_inserts_row_with_provenance(monkeypatch):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    # Force $25 notional regardless of any preview env override
    # (preview ships with PUBLIC_LIVE_NOTIONAL_USD=1 for safer canary
    # trades; this test pins the legacy $25/0.125 math).
    monkeypatch.setenv("PUBLIC_LIVE_NOTIONAL_USD", "25")
    monkeypatch.delenv("PUBLIC_LIVE_SYMBOLS", raising=False)
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "secret", "api_secret": "acct",
    }
    fake_client = MagicMock()
    fake_client.place_order.return_value = {"id": "PUB-ORDER-123", "status": "submitted"}

    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=fake_client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL",
            "direction": "BUY",
            "confidence": 0.72,
            "sovereign_decision_id": "dec-1",
            "prediction_id": "pred-1",
            "source_signal": "alpha:ml_orchestrator:v1",
        })

    assert out is not None
    assert out["broker_id"] == "public"
    assert out["symbol"] == "AAPL"
    assert out["direction"] == "LONG"
    assert out["broker_order_id"] == "PUB-ORDER-123"
    assert out["size"] == 0.125  # 25 / 200
    assert out["live_notional_usd"] == 25.0
    assert out["entry_price"] == 200.0
    assert out["confidence"] == 0.72
    assert out["sovereign_decision_id"] == "dec-1"

    # Public.com SDK was called with the right shape.
    fake_client.place_order.assert_called_once()
    kwargs = fake_client.place_order.call_args.kwargs
    assert kwargs["side"] == "buy"
    assert kwargs["order_type"] == "market"
    assert kwargs["symbol"] == "AAPL"
    assert kwargs["qty"] == 0.125

    # Mongo received the doc.
    assert len(db.equity_live_trades.docs) == 1
    assert db.equity_live_trades.docs[0]["broker_id"] == "public"


@pytest.mark.asyncio
async def test_place_order_raises_no_mongo_write(monkeypatch):
    """Even if PublicTradingService.place_order RAISES, we must not
    propagate the exception OR write a claim-of-fill to Mongo."""
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "secret", "api_secret": "acct",
    }
    fake_client = MagicMock()
    fake_client.place_order.side_effect = RuntimeError("network down")
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=fake_client,
    ):
        out = await maybe_route_live(db, intent={"symbol": "AAPL", "direction": "BUY"})
    assert out is None
    assert db.equity_live_trades.docs == []
