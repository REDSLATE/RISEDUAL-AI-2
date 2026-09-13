"""P1-A end-to-end tests — short-execution path through maybe_route_live.

Covers the wiring: router classifies open_short → executor runs the
4-rung eligibility ladder → REST helper submits SELL+OPEN with the
right whole-share qty. Also verifies close_short (BUY_TO_COVER)
bypasses the ladder (covering is an exit, not new short exposure).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.public_equity_live_executor import maybe_route_live


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.setenv("PUBLIC_LIVE_RTH_ONLY", "0")
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", "0.0")
    monkeypatch.setenv("PUBLIC_LIVE_NOTIONAL_USD", "350")
    monkeypatch.setenv("PUBLIC_LIVE_SHORT_FIRST_NOTIONAL_USD", "25")
    monkeypatch.delenv("PUBLIC_LIVE_SYMBOLS", raising=False)
    monkeypatch.setenv("PUBLIC_MAX_INTRADAY_MOVE_PCT", "0")
    yield


class _FakeColl:
    def __init__(self):
        self.docs: list[dict] = []
        self._find_one_response = None
        self.update_one_calls: list = []

    async def find_one(self, _filter, _proj=None, **_kw):
        return self._find_one_response

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


def _make_client():
    c = MagicMock()
    c.base_url = "https://api.public.com/userapigateway"
    c.account_id = "ACCT-1"
    c._auth_headers.return_value = {
        "Authorization": "Bearer TOK", "Content-Type": "application/json",
    }
    c.get_positions.return_value = []
    c.get_account.return_value = {
        "id": "ACCT-1", "cash": 1000.0, "buying_power": 1000.0, "equity": 1000.0,
    }
    # place_order shouldn't be reached on the short path; wire an
    # AssertionError so any accidental call is loud.
    c.place_order.side_effect = AssertionError(
        "SDK place_order must not be called on short paths — use REST helper"
    )
    return c


def _ladder_pass_stubs():
    """requests.get / requests.post stubs that make the 4-rung ladder pass."""
    def _get(url, **_kw):
        r = MagicMock(status_code=200, content=b"{}")
        if "trading/account" in url:
            r.json.return_value = {"accounts": [{
                "accountId": "ACCT-1",
                "brokerageAccountType": "MARGIN",
                "tradePermissions": "BUY_AND_SELL",
            }]}
        elif "marketdata/instruments" in url:
            r.json.return_value = {"shortingAvailability": "EASY_TO_BORROW"}
        else:
            r.json.return_value = {}
        return r

    def _post(url, **_kw):
        r = MagicMock(status_code=200, content=b"{}")
        if "preflight" in url:
            r.json.return_value = {
                "buyingPowerRequirement": 500.0, "marginImpact": 250.0,
                "upTickRuleRequired": False, "maxLocateQuantity": 100,
            }
        else:
            r.json.return_value = {"orderId": "PUB-SHORT-42", "status": "submitted"}
        return r

    return _get, _post


# ── Short execution disabled by default ─────────────────────────────

@pytest.mark.asyncio
async def test_open_short_skipped_when_execution_disabled(monkeypatch):
    monkeypatch.delenv("ENABLE_SHORT_EXECUTION", raising=False)
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    client = _make_client()
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=100.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ):
        # A SELL signal with no long position + no explicit BUY_TO_COVER
        # → router classifies as open_short → executor refuses
        # (ENABLE_SHORT_EXECUTION unset).
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "SELL", "confidence": 0.8,
        })
    assert out is None


# ── Happy path: ladder green, REST short fires ─────────────────────

@pytest.mark.asyncio
async def test_open_short_whole_share_yields_one_or_more(monkeypatch):
    """Sanity: at $10 mark and $25 canary budget, whole-share qty = 2.
    Verify the REST helper receives an integer qty (not fractional)."""
    monkeypatch.setenv("ENABLE_SHORT_EXECUTION", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    client = _make_client()
    _get, _post_stub = _ladder_pass_stubs()
    submit_calls: list = []

    def _submit_short(_c, **kw):
        submit_calls.append(kw)
        return {"id": "PUB-SHORT-99", "status": "submitted",
                "openCloseIndicator": "OPEN", "useMargin": True,
                "filled_qty": kw["qty"]}

    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=10.0),   # $10 mark
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ), patch(
        "services.public_short_eligibility.requests.get", side_effect=_get,
    ), patch(
        "services.public_short_eligibility.requests.post", side_effect=_post_stub,
    ), patch(
        "services.public_short_executor.submit_short_order",
        side_effect=_submit_short,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "SIRI", "direction": "SELL", "confidence": 0.8,
        })

    assert out is not None
    assert out["intent_kind"] == "open_short"
    assert submit_calls, "REST short submit must have been invoked"
    kw = submit_calls[0]
    assert kw["side"] == "SELL"
    assert kw["open_close"] == "OPEN"
    assert kw["use_margin"] is True
    # Whole-share only — no fractional shares. $25 / $10 = 2 shares.
    assert isinstance(kw["qty"], int)
    assert kw["qty"] == 2


# ── Ladder failure paths block the submit ──────────────────────────

@pytest.mark.asyncio
async def test_open_short_blocked_by_cash_account(monkeypatch):
    monkeypatch.setenv("ENABLE_SHORT_EXECUTION", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    client = _make_client()

    def _get(url, **_kw):
        r = MagicMock(status_code=200, content=b"{}")
        if "trading/account" in url:
            r.json.return_value = {"accounts": [{
                "accountId": "ACCT-1",
                "brokerageAccountType": "CASH",
                "tradePermissions": "BUY_AND_SELL",
            }]}
        else:
            r.json.return_value = {}
        return r

    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=10.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ), patch(
        "services.public_short_eligibility.requests.get", side_effect=_get,
    ), patch(
        "services.public_short_executor.submit_short_order",
    ) as submit_mock:
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "SELL", "confidence": 0.8,
        })

    assert out is None
    submit_mock.assert_not_called()


@pytest.mark.asyncio
async def test_open_short_blocked_by_not_shortable(monkeypatch):
    monkeypatch.setenv("ENABLE_SHORT_EXECUTION", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    client = _make_client()

    def _get(url, **_kw):
        r = MagicMock(status_code=200, content=b"{}")
        if "trading/account" in url:
            r.json.return_value = {"accounts": [{
                "accountId": "ACCT-1",
                "brokerageAccountType": "MARGIN",
                "tradePermissions": "BUY_AND_SELL",
            }]}
        elif "marketdata/instruments" in url:
            r.json.return_value = {"shortingAvailability": "NOT_SHORTABLE"}
        else:
            r.json.return_value = {}
        return r

    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=10.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ), patch(
        "services.public_short_eligibility.requests.get", side_effect=_get,
    ), patch(
        "services.public_short_executor.submit_short_order",
    ) as submit_mock:
        out = await maybe_route_live(db, intent={
            "symbol": "SPCX", "direction": "SELL", "confidence": 0.8,
        })

    assert out is None
    submit_mock.assert_not_called()


@pytest.mark.asyncio
async def test_open_short_blocked_by_preflight_rejection(monkeypatch):
    monkeypatch.setenv("ENABLE_SHORT_EXECUTION", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    client = _make_client()
    _get, _ = _ladder_pass_stubs()

    def _post(url, **_kw):
        r = MagicMock(status_code=200, content=b"{}")
        if "preflight" in url:
            r.json.return_value = {"rejected": True, "reason": "insufficient_margin"}
        else:
            r.json.return_value = {}
        return r

    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=10.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ), patch(
        "services.public_short_eligibility.requests.get", side_effect=_get,
    ), patch(
        "services.public_short_eligibility.requests.post", side_effect=_post,
    ), patch(
        "services.public_short_executor.submit_short_order",
    ) as submit_mock:
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "SELL", "confidence": 0.8,
        })

    assert out is None
    submit_mock.assert_not_called()


# ── close_short does NOT run the ladder (covering is an exit) ──────

@pytest.mark.asyncio
async def test_close_short_bypasses_eligibility_ladder(monkeypatch):
    """Covering an existing short is an EXIT, not a new short exposure.
    The ladder would (correctly) fail rung 2 because there IS an
    existing position. We must skip the ladder entirely for
    close_short so a legitimate cover isn't blocked."""
    monkeypatch.setenv("ENABLE_SHORT_EXECUTION", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    db.equity_live_trades._find_one_response = {
        "_id": "short-row", "size": 5.0, "trade_id": "T-1",
        "direction": "SHORT",
    }
    client = _make_client()
    client.get_positions.return_value = [
        {"symbol": "AAPL", "qty": 5.0, "side": "short"},
    ]

    submit_calls: list = []

    def _submit(_c, **kw):
        submit_calls.append(kw)
        return {"id": "COVER-99", "status": "filled",
                "openCloseIndicator": "CLOSE", "useMargin": True,
                "filled_qty": kw["qty"]}

    # Note: we do NOT patch public_short_eligibility.requests here —
    # if the executor mistakenly runs the ladder against no mock, the
    # rung 1 HTTP call would hit real network, then the test would
    # fail. That's the anti-regression signal we want.
    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=95.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ), patch(
        "services.public_short_executor.submit_short_order",
        side_effect=_submit,
    ):
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "BUY",
            "intent_action": "BUY_TO_COVER", "confidence": 0.8,
        })

    assert out is not None
    assert out["intent_kind"] == "close_short"
    assert submit_calls[0]["side"] == "BUY"
    assert submit_calls[0]["open_close"] == "CLOSE"
    assert submit_calls[0]["qty"] == 5   # whole share, from broker qty


# ── Existing position blocks a new open_short via rung 2 ────────────

@pytest.mark.asyncio
async def test_open_short_blocked_by_existing_position(monkeypatch):
    """P1-A doctrine: don't stack a fresh short on top of any existing
    position. Router only sees BUY-vs-position; SELL against nothing
    could reach open_short and rung 2 must catch it if a position
    exists but wasn't tracked in Mongo."""
    monkeypatch.setenv("ENABLE_SHORT_EXECUTION", "1")
    db = _FakeDB()
    db.broker_connections._find_one_response = {
        "api_key": "sk", "api_secret": "acct"
    }
    client = _make_client()
    # Broker reports a residual short position that the Mongo idempotency
    # check missed (row absent). We do NOT include the position in the
    # early probe (that would classify as close_short — the correct
    # path). Instead simulate: probe returns [] (stale), but the ladder's
    # rung 2 probe returns the position. This edge case is why rung 2
    # exists: ladder-level truth over any inference.
    positions_seq = iter([[], [{"symbol": "AAPL", "qty": 3.0, "side": "short"}]])
    client.get_positions.side_effect = lambda: next(positions_seq)

    def _get(url, **_kw):
        r = MagicMock(status_code=200, content=b"{}")
        if "trading/account" in url:
            r.json.return_value = {"accounts": [{
                "accountId": "ACCT-1",
                "brokerageAccountType": "MARGIN",
                "tradePermissions": "BUY_AND_SELL",
            }]}
        else:
            r.json.return_value = {}
        return r

    with patch(
        "services.public_equity_live_executor._fetch_mark_price",
        new=AsyncMock(return_value=10.0),
    ), patch(
        "services.public_equity_live_executor._public_client",
        return_value=client,
    ), patch(
        "services.public_short_eligibility.requests.get", side_effect=_get,
    ), patch(
        "services.public_short_executor.submit_short_order",
    ) as submit_mock:
        out = await maybe_route_live(db, intent={
            "symbol": "AAPL", "direction": "SELL", "confidence": 0.8,
        })

    assert out is None
    submit_mock.assert_not_called()
