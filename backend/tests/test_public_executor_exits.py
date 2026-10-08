"""P1-B tests — position-aware exit lifecycle for the Public equity executor.

Covers:
    * SELL_TO_CLOSE against an open long → close_long path uses broker qty
    * BUY_TO_COVER against an open short → close_short path
    * SELL against no position + SUPPORTS_SHORT_SALES=False → short_signal_only
    * exit_only intent with no matching position → router_no_op
    * BUY against an open short → router flips to close_short (BUY_TO_COVER),
      never opens a new long
    * SELL against an open long produces a SELL order, not a SHORT
    * Partial fills and acknowledgements stay pending until broker reconciliation
    * close_in_flight idempotency blocks concurrent re-fires
    * Broker qty overrides Mongo-believed qty when they diverge
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.public_equity_live_executor import maybe_route_live


@pytest.fixture(autouse=True)
def _base_env(monkeypatch, tmp_path):
    """Enable live-exec + disable time-of-day / capability gates."""
    monkeypatch.setenv("ALPHA_V2_DB", str(tmp_path / "receipts.sqlite"))
    from services import alpha_hardware_kill_switch
    monkeypatch.setattr(alpha_hardware_kill_switch, "check", lambda: (False, None))
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.setenv("PUBLIC_LIVE_RTH_ONLY", "0")
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "0.0")
    monkeypatch.setenv("PUBLIC_LIVE_NOTIONAL_USD", "25")
    monkeypatch.delenv("PUBLIC_LIVE_SYMBOLS", raising=False)
    monkeypatch.setenv("PUBLIC_MAX_INTRADAY_MOVE_PCT", "0")  # disable chasing filter
    yield


class _FakeColl:
    def __init__(self):
        self.docs: list[dict] = []
        self._find_one_response = None
        self.find_one_calls: list = []
        self.update_one_calls: list = []

    async def find_one(self, _filter, _proj=None, **kw):
        self.find_one_calls.append(dict(_filter))
        return self._find_one_response

    def find(self, _filter):
        async def rows():
            if False:
                yield None
        return rows()

    async def insert_one(self, doc):
        self.docs.append(doc)
        return MagicMock(inserted_id="fake")

    async def update_one(self, filter_, update, **_kw):
        self.update_one_calls.append((dict(filter_), dict(update)))
        return MagicMock(matched_count=1, modified_count=1)


class _FakeDB:
    def __init__(self):
        self.broker_connections = _FakeColl()
        self.equity_live_trades = _FakeColl()
        self.intent_skip_log = _FakeColl()

    def __getitem__(self, k):
        return getattr(self, k)


def _make_client(*, positions=None, order_response=None, account=None):
    c = MagicMock()
    # Real string attrs so the REST short helper can build valid URLs.
    c.base_url = "https://api.public.com/userapigateway"
    c.account_id = "ACCT-TEST"
    c._auth_headers.return_value = {
        "Authorization": "Bearer TOK", "Content-Type": "application/json",
    }
    c.get_positions.return_value = positions or []
    c.get_orders.return_value = []
    c.get_account.return_value = account or {
        "id": "ACCT", "cash": 500.0, "buying_power": 500.0, "equity": 500.0,
    }
    c.place_order.return_value = order_response or {
        "id": "PUB-CLOSE-1", "status": "filled",
    }
    return c


# ── SELL against an open long → close_long ─────────────────────────

@pytest.mark.asyncio
async def test_sell_closes_long_uses_broker_qty(monkeypatch):
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    # Mongo believes we hold 2.3 shares; broker reports 1.8 → broker wins.
    db.equity_live_trades._find_one_response = {
        "_id": "existing-long", "size": 2.3, "trade_id": "T1",
        "direction": "LONG",
    }
    client = _make_client(
        positions=[{"symbol": "AAPL", "qty": 1.8, "side": "long"}],
        order_response={"id": "SELL-1", "status": "filled",
                        "filled_qty": 1.8, "fillPrice": 205.0},
    )
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=205.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "SELL", "confidence": 0.8,
        })

    assert out is not None
    assert out["intent_kind"] == "close_long"
    assert out["direction"] == "LONG"
    assert out["side"] == "SELL"
    assert out["size"] == 1.8              # broker qty won, not Mongo's 2.3
    assert out["filled_qty"] == 1.8
    assert out["remaining_qty"] == 1.8  # pre-submit holdings, not a fabricated flat
    assert out["status"] == "close_pending"
    # Broker was asked to SELL at the broker qty.
    kwargs = client.place_order.call_args.kwargs
    assert kwargs["side"] == "sell"
    assert kwargs["qty"] == 1.8


# ── BUY_TO_COVER against an open short → close_short ───────────────

@pytest.mark.asyncio
async def test_explicit_buy_to_cover_closes_short(monkeypatch):
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    db.equity_live_trades._find_one_response = {
        "_id": "existing-short", "size": 3.0, "trade_id": "T2",
        "direction": "SHORT",
    }
    client = _make_client(
        positions=[{"symbol": "AAPL", "qty": 3.0, "side": "short"}],
    )
    # close_short → REST helper. Mock the helper directly so we don't
    # care about HTTP transport.
    submit_calls: list = []

    def _submit(_c, **kw):
        submit_calls.append(kw)
        return {"id": "COVER-1", "status": "filled",
                "openCloseIndicator": "CLOSE", "useMargin": True,
                "filled_qty": kw["qty"]}

    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=195.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ), patch(
        "services.public_equity_live_executor.submit_short_order"
        if False else "services.public_short_executor.submit_short_order",
        side_effect=_submit,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "BUY",
            "intent_action": "BUY_TO_COVER", "confidence": 0.8,
        })

    assert out is not None
    assert out["intent_kind"] == "close_short"
    assert out["direction"] == "SHORT"
    assert out["side"] == "BUY"
    assert out["size"] == 3.0
    assert out["status"] == "close_pending"
    # REST helper was called with the right shape: BUY + CLOSE.
    kw = client.place_order.call_args.kwargs
    assert kw["side"] == "buy"
    assert kw["open_close_indicator"] == "CLOSE"
    assert kw["use_margin"] is True
    assert kw["qty"] == 3


# ── BUY signal against existing short: router flips to close_short ─

@pytest.mark.asyncio
async def test_buy_against_open_short_covers_never_opens_long(monkeypatch):
    """P1-B safety property: a BUY signal against an existing short
    must cover (BUY_TO_COVER), never open a new long."""
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    db.equity_live_trades._find_one_response = {
        "_id": "existing-short", "size": 2.0, "trade_id": "T3",
        "direction": "SHORT",
    }
    client = _make_client(
        positions=[{"symbol": "AAPL", "qty": 2.0, "side": "short"}],
    )
    submit_calls: list = []

    def _submit(_c, **kw):
        submit_calls.append(kw)
        return {"id": "COVER-2", "status": "filled",
                "openCloseIndicator": "CLOSE", "useMargin": True,
                "filled_qty": kw["qty"]}

    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ), patch(
        "services.public_short_executor.submit_short_order",
        side_effect=_submit,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "BUY", "confidence": 0.8,
        })

    assert out is not None
    assert out["intent_kind"] == "close_short"
    kw = client.place_order.call_args.kwargs
    assert kw["side"] == "buy" and kw["open_close_indicator"] == "CLOSE"
    assert kw["qty"] == 2  # covering existing exposure, never a new long


# ── SELL against no position + shorts disabled → short_signal_only ─

@pytest.mark.asyncio
async def test_sell_no_position_without_short_support_is_skipped(monkeypatch):
    monkeypatch.delenv("PUBLIC_LIVE_SUPPORTS_SHORTS", raising=False)
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    # No mongo row, no broker position.
    client = _make_client(positions=[])
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "SELL", "confidence": 0.8,
        })

    assert out is None
    # place_order must NEVER be called for an unbacked short.
    client.place_order.assert_not_called()


# ── exit_only guard: no matching position → no_op ──────────────────

@pytest.mark.asyncio
async def test_exit_only_with_no_position_is_noop(monkeypatch):
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    client = _make_client(positions=[])
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "BUY", "exit_only": True,
            "confidence": 0.8,
        })

    assert out is None
    client.place_order.assert_not_called()


# ── Partial fill → row stays open with residual ────────────────────

@pytest.mark.asyncio
async def test_partial_close_marks_row_partial(monkeypatch):
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    db.equity_live_trades._find_one_response = {
        "_id": "existing-long", "size": 5.0, "trade_id": "T4",
        "direction": "LONG",
    }
    # Broker fills only 3 of the requested 5.
    client = _make_client(
        positions=[{"symbol": "AAPL", "qty": 5.0, "side": "long"}],
        order_response={"id": "PARTIAL-1", "status": "partially_filled",
                        "filled_qty": 3.0},
    )
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "SELL", "confidence": 0.8,
        })

    assert out is not None
    assert out["status"] == "close_pending"
    assert out["filled_qty"] == 3.0
    assert out["remaining_qty"] == 5.0  # holdings are authoritative on the next poll
    assert out["closed_at"] is None
    updates = [c[1]["$set"] for c in db.equity_live_trades.update_one_calls]
    assert updates[-1]["close_pending"] is True
    assert updates[-1]["close_filled_qty"] == 3.0
    assert "closed_at" not in updates[-1]


# ── close_in_flight idempotency ────────────────────────────────────

@pytest.mark.asyncio
async def test_close_in_flight_blocks_concurrent_close(monkeypatch):
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    # A close was started 5 seconds ago and is still in flight.
    db.equity_live_trades._find_one_response = {
        "_id": "existing-long", "size": 2.0, "trade_id": "T5",
        "direction": "LONG",
        "close_in_flight_at": datetime.now(timezone.utc) - timedelta(seconds=5),
    }
    client = _make_client(
        positions=[{"symbol": "AAPL", "qty": 2.0, "side": "long"}],
    )
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "SELL", "confidence": 0.8,
        })

    assert out is None
    # place_order MUST NOT be called while a close is in flight.
    client.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_close_in_flight_never_expires_without_broker_reconciliation(monkeypatch):
    """Elapsed time cannot prove an order failed or permit a second close."""
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    # Flag is 200 seconds old (past the 120s stale window).
    db.equity_live_trades._find_one_response = {
        "_id": "existing-long", "size": 2.0, "trade_id": "T6",
        "direction": "LONG",
        "close_in_flight_at": datetime.now(timezone.utc) - timedelta(seconds=200),
    }
    client = _make_client(
        positions=[{"symbol": "AAPL", "qty": 2.0, "side": "long"}],
        order_response={"id": "SELL-STALE", "status": "filled",
                        "filled_qty": 2.0},
    )
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "SELL", "confidence": 0.8,
        })

    assert out is None
    client.place_order.assert_not_called()


# ── open_long against existing short is refused (no auto-reverse) ──

@pytest.mark.asyncio
async def test_explicit_open_long_blocked_by_open_short(monkeypatch):
    """P1-B: never auto-reverse from short → long. Operator must close
    the short explicitly, then open a new long as a second action."""
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    db.equity_live_trades._find_one_response = {
        "_id": "existing-short", "size": 2.0, "trade_id": "T7",
        "direction": "SHORT",
    }
    client = _make_client(
        positions=[{"symbol": "AAPL", "qty": 2.0, "side": "short"}],
    )
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=200.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "BUY",
            "intent_action": "OPEN_LONG", "confidence": 0.8,
        })

    assert out is None
    client.place_order.assert_not_called()
