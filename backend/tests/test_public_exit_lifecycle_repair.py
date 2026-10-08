"""Offline broker failure corpus: no credentials or network are used."""
import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.alpha_core_v2.broker import PublicBroker, _normalize_order
from services.alpha_core_v2.config import Config
from services.alpha_core_v2.contracts import (
    ExecutionQuote, OrderResult, Outcome, PositionState, Receipt, Stage,
)
from services.alpha_core_v2.engine import CoreV2Engine
from services.alpha_core_v2.exit_policy import ExitPolicyRunner
from services.alpha_core_v2.receipts import ReceiptStore
from tests.test_alpha_core_v2 import FakeBroker, _cand, _cfg


def engine(broker, path=":memory:"):
    return CoreV2Engine(broker, ReceiptStore(str(path)), _cfg())


def broker(status="accepted", qty=1.0, side="long"):
    return FakeBroker(positions=[PositionState("AAA", qty, side)],
                      submit_result=lambda s, q: OrderResult(True, "sell-id", status,
                                                            requested_qty=q))


@pytest.mark.asyncio
async def test_ack_never_means_closed():
    b = broker(); e = engine(b)
    r = await e.close_position("AAA")
    assert r.outcome is Outcome.TRADED
    assert not r.position_reconciled
    assert r.broker_reported_fill_qty == 0
    assert e.store.outstanding_orders()[0]["position_status"] == "closing"


@pytest.mark.asyncio
async def test_new_engine_and_restart_cannot_duplicate_close(tmp_path):
    b = broker(); path = tmp_path / "state.sqlite"
    await engine(b, path).close_position("AAA")
    r = await engine(b, path).close_position("AAA")
    assert r.reason == "close_in_flight"
    assert len(b.submitted) == 1


@pytest.mark.asyncio
async def test_partial_fill_keeps_guard_even_when_broker_flat():
    b = broker(); e = engine(b)
    await e.close_position("AAA")
    b._order_result = OrderResult(True, "sell-id", "partially_filled", filled_qty=.4, fill_price=10)
    b._positions = []
    assert (await e.reconcile_outstanding())["finalized"] == 0
    assert e.store.outstanding_orders()[0]["broker_reported_fill_qty"] == .4
    assert (await e.close_position("AAA")).reason == "close_in_flight"


@pytest.mark.asyncio
async def test_full_fill_waits_for_holdings_snapshot():
    b = broker(); e = engine(b)
    await e.close_position("AAA")
    b._order_result = OrderResult(True, "sell-id", "filled", filled_qty=1, fill_price=10)
    assert (await e.reconcile_outstanding())["finalized"] == 0
    b._positions = []
    assert (await e.reconcile_outstanding())["finalized"] == 1
    assert not e.store.outstanding_orders()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["rejected", "cancelled", "expired"])
async def test_terminal_unfilled_exit_preserves_position(status):
    b = broker(); e = engine(b)
    r = await e.close_position("AAA")
    b._order_result = OrderResult(False, "sell-id", status)
    await e.reconcile_outstanding()
    saved = e.store.get_receipt(r.receipt_id)
    assert saved["position_status"] == "open"
    assert saved["reconciled_position_qty"] == 1
    assert saved["position_reconciled"]


@pytest.mark.asyncio
async def test_cancelled_partial_exit_retries_only_fresh_residual():
    b = broker(); e = engine(b)
    await e.close_position("AAA")
    b._order_result = OrderResult(False, "sell-id", "cancelled", filled_qty=.4, fill_price=12)
    b._positions = [PositionState("AAA", .6, "long")]
    await e.reconcile_outstanding()
    r = await e.close_position("AAA")
    assert r.requested_qty == .6
    assert b.submitted[-1] == ("AAA", .6, "sell")


@pytest.mark.asyncio
async def test_timeout_is_journaled_before_http_and_survives_restart(tmp_path):
    path = tmp_path / "state.sqlite"
    class Timeout(FakeBroker):
        def submit(self, sym, qty, side="buy", **kwargs):
            rows = ReceiptStore(str(path)).outstanding_orders()
            assert rows[0]["order_id"] == kwargs["client_order_id"]
            assert kwargs["close"] is True
            raise TimeoutError("ACK lost")
    b = Timeout(positions=[PositionState("AAA", 1)])
    r = await engine(b, path).close_position("AAA")
    assert r.outcome is Outcome.FAILED and r.order_id
    import uuid
    uuid.UUID(r.order_id)
    assert (await engine(b, path).close_position("AAA")).reason == "close_in_flight"


@pytest.mark.asyncio
async def test_unknown_status_or_missing_order_never_releases_guard():
    b = broker(); e = engine(b)
    await e.close_position("AAA")
    for response in (None, OrderResult(False, "sell-id", "unknown")):
        b._order_result = response
        assert (await e.reconcile_outstanding())["finalized"] == 0
        assert (await e.close_position("AAA")).reason == "close_in_flight"


@pytest.mark.asyncio
async def test_outstanding_buy_blocks_exit_and_new_entry(tmp_path):
    b = FakeBroker(submit_result=lambda s, q: OrderResult(True, "buy-id", "accepted"))
    path = tmp_path / "state.sqlite"
    e = engine(b, path)
    await e._process("c", _cand(), b.get_account(), live=True)
    b._positions = [PositionState("AAA", .5)]
    restarted = engine(b, path)
    assert (await restarted.close_position("AAA")).reason == "close_in_flight"
    assert (await restarted._process("c", _cand(), b.get_account(), live=True)).outcome is Outcome.BLOCKED
    assert len(b.submitted) == 1


@pytest.mark.asyncio
async def test_close_long_never_sells_short_position():
    b = broker(side="short"); e = engine(b)
    assert (await e.close_position("AAA")).reason == "position_side_mismatch"
    assert not b.submitted
    await e.close_position("AAA", expected_side="short")
    assert b.submitted[-1][2] == "buy"


@pytest.mark.asyncio
async def test_broker_open_orders_and_unknown_lookup_block_close():
    b = broker(); e = engine(b)
    b.get_open_orders = lambda sym: [{"orderId": "external"}]
    assert (await e.close_position("AAA")).reason == "broker_order_in_flight"
    def broken(sym):
        raise RuntimeError("503")
    b.get_open_orders = broken
    assert (await e.close_position("AAA")).outcome is Outcome.FAILED
    assert not b.submitted


def test_atomic_reservation_across_connections(tmp_path):
    path = str(tmp_path / "state.sqlite")
    def receipt(n):
        return Receipt(str(n), "c", "AAA", time.time_ns(), Outcome.FAILED, Stage.ORDER,
                       order_id=str(n), position_status="closing")
    stores = [ReceiptStore(path), ReceiptStore(path)]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda n: stores[n].reserve_order(receipt(n)), range(2)))
    assert sum(results) == 1


def test_adapter_requests_strict_holdings_and_orders():
    client = Mock()
    client.get_positions.return_value = []
    client.get_orders.return_value = []
    b = PublicBroker(client)
    b.get_positions(); b.get_open_orders("AAA")
    client.get_positions.assert_called_once_with(strict=True)
    client.get_orders.assert_called_once_with(status="open", strict=True)


@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED", "REJECTED"])
def test_terminal_status_keeps_cumulative_fills(status):
    r = _normalize_order({"orderId": "x", "status": status,
                          "filledQuantity": ".4", "averagePrice": "12"}, requested_qty=1)
    assert r.status == status.lower()
    assert r.filled_qty == .4 and r.fill_price == 12


def test_fill_quantity_never_falls_back_to_requested_quantity():
    r = _normalize_order({"orderId": "x", "status": "NEW", "quantity": "1"}, requested_qty=1)
    assert r.filled_qty == 0


def test_phantom_reconcile_updates_payload_and_excludes_old_anchor():
    e = engine(broker())
    e.store.save(Receipt("entry", "c", "AAA", time.time_ns(), Outcome.TRADED, Stage.CONFIRM,
                         position_status="open", fill_price=100, position_reconciled=True))
    e.store.mark_reconciled_flat("AAA")
    assert e.store.get_receipt("entry")["position_status"] == "reconciled_flat"
    assert e.store.last_entry_price("AAA") == 0


def add_anchor(e, price=100, created=None, receipt_id="entry"):
    e.store.save(Receipt(receipt_id, "c", "AAA", created or time.time_ns(), Outcome.TRADED,
                         Stage.RECONCILE, position_status="open", fill_price=price,
                         position_reconciled=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [None, 60])
async def test_stop_policy_rejects_unknown_or_stale_quote(age):
    b = broker(); b._exec_price = 90; b._quote_no_ts = age is None; b._quote_age_s = age or 0
    e = engine(b); add_anchor(e)
    r = await ExitPolicyRunner().run(e, force=True)
    assert "stale" in r["actions"][0]["decision"]
    assert not b.submitted


@pytest.mark.asyncio
async def test_stop_submission_is_not_reported_as_exited():
    b = broker(); b._exec_price = 90
    e = engine(b); add_anchor(e)
    r = await ExitPolicyRunner().run(e, force=True)
    assert r["actions"][0]["close_submitted"]
    assert not r["actions"][0]["exited"]


@pytest.mark.asyncio
async def test_trailing_peak_persists_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_V2_TRAIL_PCT", ".02")
    monkeypatch.setenv("ALPHA_V2_TAKE_PROFIT_PCT", "0")
    b = broker(); b._exec_price = 110; path = tmp_path / "state.sqlite"
    e = engine(b, path); add_anchor(e)
    await ExitPolicyRunner().run(e, force=True)
    b._exec_price = 107
    result = await ExitPolicyRunner().run(engine(b, path), force=True)
    assert result["actions"][0]["decision"].startswith("trailing_stop")
    assert b.submitted


@pytest.mark.asyncio
async def test_max_hold_clock_uses_persisted_entry_time(monkeypatch):
    monkeypatch.setenv("ALPHA_V2_MAX_HOLD_S", "3600")
    b = broker(); b._exec_price = 100
    e = engine(b); add_anchor(e, created=time.time_ns() - 4000 * 10**9)
    r = await ExitPolicyRunner().run(e, force=True)
    assert r["actions"][0]["decision"].startswith("max_hold")


def test_new_entry_resets_peak_and_clock():
    store = ReceiptStore(":memory:")
    assert store.track_exit("AAA", "old", 110, 1000, 900) == (110, 900)
    assert store.track_exit("AAA", "new", 100, 2000, 1990) == (100, 1990)


@pytest.mark.asyncio
async def test_policy_skips_shorts_without_order():
    b = broker(side="short"); b._exec_price = 50; e = engine(b); add_anchor(e)
    assert (await ExitPolicyRunner().run(e, force=True))["positions"] == 0
    assert not b.submitted


def public_client():
    from services.broker_service import PublicTradingService
    client = PublicTradingService("secret", "account")
    client._auth_headers = lambda: {"Authorization": "Bearer offline"}
    return client


def response(payload, status=200):
    r = Mock()
    r.status_code = status; r.content = b"json"; r.text = "offline"
    r.json.return_value = payload
    return r


def test_public_http_preserves_reserved_uuid_and_close_indicator(monkeypatch):
    import uuid
    oid = str(uuid.uuid4()); posts = []
    def post(url, **kwargs):
        posts.append((url, kwargs["json"]))
        return response({"orderId": oid})
    monkeypatch.setattr("services.broker_service.requests.post", post)
    result = public_client().place_order("AAA", .6, "sell", client_order_id=oid,
                                         open_close_indicator="CLOSE")
    assert result["id"] == oid and result["status"] == "submitted"
    assert posts[0][1]["orderId"] == oid
    assert posts[0][1]["openCloseIndicator"] == "CLOSE"
    assert posts[0][1]["quantity"] == "0.6"
    assert "filled_qty" not in result


def test_public_http_timeout_keeps_actual_uuid(monkeypatch):
    import uuid
    oid = str(uuid.uuid4())
    def timeout(*args, **kwargs):
        raise TimeoutError("ACK lost")
    monkeypatch.setattr("services.broker_service.requests.post", timeout)
    result = public_client().place_order("AAA", 1, "sell", client_order_id=oid)
    assert result["id"] == oid and result["status"] == "unknown"


@pytest.mark.parametrize("payload", [{}, {"positions": None}, {"positions": [{"instrument": {"symbol": "AAA"}, "quantity": "NaN"}]}])
def test_public_strict_position_response_cannot_fabricate_flat(monkeypatch, payload):
    monkeypatch.setattr("services.broker_service.requests.get", lambda *a, **k: response(payload))
    with pytest.raises(ValueError):
        public_client().get_positions(strict=True)


def test_public_strict_position_auth_failure_raises():
    client = public_client(); client._auth_headers = lambda: None
    with pytest.raises(RuntimeError):
        client.get_positions(strict=True)


def test_public_open_order_lookup_uses_portfolio_and_keeps_unknown_states(monkeypatch):
    seen = []
    def get(url, **kw):
        seen.append(url)
        return response({"orders": [{"orderId": "a", "status": "PARTIALLY_FILLED"},
                                    {"orderId": "b", "status": "FILLED"},
                                    {"orderId": "c", "status": "PENDING_CANCEL"}]})
    monkeypatch.setattr("services.broker_service.requests.get", get)
    orders = public_client().get_orders(status="open", strict=True)
    assert seen[0].endswith("/trading/account/portfolio/v2")
    assert [o["orderId"] for o in orders] == ["a", "c"]


@pytest.mark.asyncio
async def test_legacy_protective_exit_bypasses_entry_filters(monkeypatch, tmp_path):
    from tests.test_public_executor_exits import _FakeDB, _make_client
    from services import alpha_hardware_kill_switch as hw
    from services.public_equity_live_executor import maybe_route_live
    monkeypatch.setenv("ALPHA_V2_DB", str(tmp_path / "receipts.sqlite"))
    monkeypatch.setenv("RISEDUAL_PUBLIC_LIVE_EXEC", "1")
    monkeypatch.delenv("ALPHA_CORE_V2", raising=False)
    monkeypatch.setenv("PUBLIC_LIVE_RTH_ONLY", "0")
    monkeypatch.setenv("PUBLIC_LIVE_CONFIDENCE_FLOOR", ".99")
    monkeypatch.setenv("PUBLIC_LIVE_SYMBOLS", "ZZZ")
    monkeypatch.setattr(hw, "check", lambda: (False, None))
    db = _FakeDB(); db.broker_connections._find_one_response = {"api_key": "sk", "api_secret": "acct"}
    client = _make_client(positions=[{"symbol": "AAA", "qty": .5, "side": "long"}],
                          order_response={"id": "ack", "status": "submitted"})
    monkeypatch.setattr("services.public_equity_live_executor._public_client", lambda *a: client)
    out = await maybe_route_live(db, intent={"symbol": "AAA", "direction": "SELL", "exit_only": True, "confidence": 0})
    assert out["status"] == "close_pending" and out["filled_qty"] == 0
    assert out["closed_at"] is None
    assert client.place_order.call_args.kwargs["open_close_indicator"] == "CLOSE"


@pytest.mark.asyncio
async def test_periodic_legacy_reconciliation_closes_only_after_broker_flat():
    from services.public_exit_lifecycle import reconcile_exits
    b = broker(); e = engine(b)
    r = await e.close_position("AAA")
    updates = []
    class Collection:
        def find(self, query):
            async def rows():
                yield {"_id": "mongo", "close_receipt_id": r.receipt_id, "size": 1}
            return rows()
        async def update_one(self, query, mutation):
            updates.append(mutation)
    db = SimpleNamespace(equity_live_trades=Collection())
    b._order_result = OrderResult(True, "sell-id", "partially_filled", filled_qty=.4, fill_price=12)
    b._positions = [PositionState("AAA", .6)]
    await reconcile_exits(db, None, engine=e)
    assert updates[-1]["$set"]["status"] == "open"
    assert updates[-1]["$set"]["close_pending"]
    assert "$unset" not in updates[-1]
    b._order_result = OrderResult(True, "sell-id", "filled", filled_qty=1, fill_price=12)
    b._positions = []
    await reconcile_exits(db, None, engine=e)
    assert updates[-1]["$set"]["status"] == "closed"
    assert updates[-1]["$set"]["close_price"] == 12
    assert "$unset" in updates[-1]


def test_public_adapter_hardware_guard_blocks_http(monkeypatch):
    from services import alpha_hardware_kill_switch as hw
    monkeypatch.setattr(hw, "check", lambda: (True, "operator"))
    client = Mock()
    result = PublicBroker(client).submit("AAA", 1, "sell", close=True)
    assert not result.ok and "hw_kill_switch" in result.error
    client.place_order.assert_not_called()


def test_public_adapter_session_guard_blocks_http(monkeypatch):
    from services import alpha_hardware_kill_switch as hw
    monkeypatch.setattr(hw, "check", lambda: (False, None))
    monkeypatch.setattr("services.public_equity_live_executor._rth_only_enabled", lambda: True)
    monkeypatch.setattr("services.public_equity_live_executor._in_regular_session", lambda: False)
    client = Mock()
    result = PublicBroker(client).submit("AAA", 1, "sell", close=True)
    assert not result.ok and result.error == "market_closed"
    client.place_order.assert_not_called()
