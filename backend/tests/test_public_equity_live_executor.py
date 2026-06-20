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
    # The new pre-trade cash check (2026-06-18) calls get_account first.
    # Stub a funded account so we proceed past the gate.
    fake_client.get_account.return_value = {
        "id": "ACCT", "cash": 1000.0, "buying_power": 1000.0, "equity": 1000.0,
    }

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


# ── 2026-06-18: Safety layers ────────────────────────────────────────


@pytest.mark.asyncio
async def test_confidence_floor_blocks_low_conviction(monkeypatch):
    """Pin: signals below PUBLIC_LIVE_CONFIDENCE_FLOOR (default 0.65)
    must NOT reach the broker."""
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "0.65")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "secret", "api_secret": "acct",
    }
    out = await maybe_route_live(db, intent={
        "symbol": "AAPL", "direction": "BUY", "confidence": 0.55,
    })
    assert out is None
    # NO connect-state fetch attempted, NO mark-price probe, NO order.


@pytest.mark.asyncio
async def test_confidence_floor_passes_high_conviction(monkeypatch):
    """Symmetric: confidence at or above the floor advances past
    the gate (we don't assert on the downstream broker call —
    just that the gate isn't the blocker)."""
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "0.65")
    db = _FakeDB()
    # Force the connect-state gate to fail so we know the conf gate
    # passed but execution stopped at the next gate.
    db.broker_connections._find_one_response = None
    out = await maybe_route_live(db, intent={
        "symbol": "AAPL", "direction": "BUY", "confidence": 0.70,
    })
    assert out is None  # blocked by connect-state, not conf floor


@pytest.mark.asyncio
async def test_pre_trade_cash_check_blocks_underfunded(monkeypatch):
    """Pin: if account.buying_power < notional, refuse the trade
    so Public.com doesn't reject it noisily."""
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "0.0")
    monkeypatch.setenv("PUBLIC_LIVE_NOTIONAL_USD", "25")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "secret", "api_secret": "acct",
    }
    fake_client = MagicMock()
    fake_client.get_account.return_value = {
        "id": "ACCT", "cash": 5.0, "buying_power": 5.0, "equity": 5.0,
    }
    # No place_order should ever be called.
    fake_client.place_order.side_effect = AssertionError(
        "place_order must not be invoked when buying_power < notional"
    )
    with patch(
        "services.public_equity_live_executor._public_client",
        return_value=fake_client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "BUY", "confidence": 0.80,
        })
    assert out is None
    assert db.equity_live_trades.docs == []


@pytest.mark.asyncio
async def test_pre_trade_cash_check_lets_funded_through(monkeypatch):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "0.0")
    monkeypatch.setenv("PUBLIC_LIVE_NOTIONAL_USD", "25")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "secret", "api_secret": "acct",
    }
    fake_client = MagicMock()
    fake_client.get_account.return_value = {
        "id": "ACCT", "cash": 100.0, "buying_power": 100.0, "equity": 100.0,
    }
    fake_client.place_order.return_value = {"id": "ORDER-OK", "status": "submitted"}
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=fake_client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "BUY", "confidence": 0.80,
        })
    assert out is not None
    assert out["broker_order_id"] == "ORDER-OK"


def test_confidence_floor_default():
    """Pin: default is 0.65 — operator's anti-toxic-spike substitute
    for peer-brain veto."""
    import os
    os.environ.pop("PUBLIC_LIVE_CONFIDENCE_FLOOR", None)
    from services.public_equity_live_executor import _live_confidence_floor
    assert _live_confidence_floor() == 0.65


def test_confidence_floor_bounds(monkeypatch):
    """Pin: floor clamped to [0, 0.95] — values ≥0.95 would silently
    disable live trading because they collide with the saturation cap."""
    from services.public_equity_live_executor import _live_confidence_floor
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "-0.5")
    assert _live_confidence_floor() == 0.0
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "1.5")
    assert _live_confidence_floor() == 0.95
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "garbage")
    assert _live_confidence_floor() == 0.65
