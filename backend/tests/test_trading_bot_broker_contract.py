"""Contract net for ``trading_bot_service`` broker call surface.

Pre-Step-4E safety harness (operator-mandated 2026-05-09). Pins the
**observable behaviour** of the two broker-adjacent helpers that
will move in Step 4E:

  * ``_execute_bot_trade``  — paper / live router + broker call
  * ``_apply_bot_risk_guards`` — pre-flight risk-circuit breaker

Snapshot scope (per operator brief):
  1. Broker-call ORDER (db check → conn lookup → client fetch →
     place_order).
  2. ERROR HANDLING (DB-None, no-broker-conn, falsy-result,
     exception path).
  3. RETURN SHAPES (success dict keys/values; error dict keys/values).
  4. SKIPPED / FAILED states (paper passthrough; unknown-mode dict).

These tests do NOT touch a live broker, do NOT mutate any env
flag (``BROKER_LIVE_ORDER_ENABLED`` stays ``false``), do NOT
require Mongo. Everything is monkey-patched at the call boundary.

If 4E breaks any of these contracts, the test that maps to the
broken contract will name the violation directly in its message.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import trading_bot_service as tbs


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


class _FakeBrokerConnections:
    """Mimics ``_db.broker_connections.find_one`` for live-path tests."""

    def __init__(self, returns):
        self._returns = returns
        self.find_one_calls: list[tuple] = []

    async def find_one(self, *args, **kwargs):
        self.find_one_calls.append((args, kwargs))
        return self._returns


class _FakeDB:
    def __init__(self, broker_conn=None):
        self.broker_connections = _FakeBrokerConnections(returns=broker_conn)


@pytest.fixture
def disable_risk_guard(monkeypatch):
    """Bypass risk-guard so qty/ctx pass through unchanged.

    Lets each contract test drive ``_execute_bot_trade`` with a known
    qty without entangling the risk-guard branch (which has its own
    contract section below).
    """
    async def _passthrough(bot, user_id, qty):
        return qty, {"risk_reduced": False, "original_qty": qty,
                     "adjusted_qty": qty, "reason": None}
    monkeypatch.setattr(tbs, "_apply_bot_risk_guards", _passthrough)


# ──────────────────────────────────────────────────────────────────────
# 1. Paper-mode contract
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paper_mode_delegates_to_paper_service_with_uppercase_side(
    monkeypatch, disable_risk_guard,
):
    """Paper path passes ``side.upper()`` and forwards SL/TP kwargs.

    Pins the exact arg order/case the paper service expects.
    """
    captured: dict = {}

    async def _fake_execute_trade(user_id, symbol, side, qty,
                                   stop_loss=None, take_profit=None):
        captured.update(
            user_id=user_id, symbol=symbol, side=side, qty=qty,
            stop_loss=stop_loss, take_profit=take_profit,
        )
        return {"trade_id": "p-123", "status": "filled"}

    import services.paper_trading_service as pts
    monkeypatch.setattr(pts, "execute_trade", _fake_execute_trade)

    bot = {"mode": "paper", "user_id": "u1", "name": "PaperBot"}
    result = await tbs._execute_bot_trade(
        bot=bot, symbol="aapl", side="buy", qty=10, price=150.0,
        stop_loss=145.0, take_profit=155.0,
    )

    assert captured == {
        "user_id": "u1", "symbol": "aapl", "side": "BUY", "qty": 10,
        "stop_loss": 145.0, "take_profit": 155.0,
    }
    # Passthrough — paper path returns whatever paper service returns
    assert result == {"trade_id": "p-123", "status": "filled"}


@pytest.mark.asyncio
async def test_paper_mode_never_touches_broker_imports(
    monkeypatch, disable_risk_guard,
):
    """Paper path MUST NOT import ``routes.broker`` or query
    ``broker_connections``. Pins the firewall between paper and live.
    """
    fake_db = _FakeDB(broker_conn={"broker_id": "should-not-be-read"})
    monkeypatch.setattr(tbs, "_db", fake_db)

    async def _fake_execute_trade(*a, **kw):
        return {"status": "ok"}
    import services.paper_trading_service as pts
    monkeypatch.setattr(pts, "execute_trade", _fake_execute_trade)

    bot = {"mode": "paper", "user_id": "u1"}
    await tbs._execute_bot_trade(bot=bot, symbol="AAPL",
                                 side="buy", qty=1, price=100.0)

    # broker_connections must NOT have been queried during paper path
    assert fake_db.broker_connections.find_one_calls == []


# ──────────────────────────────────────────────────────────────────────
# 2. Live-mode happy path — call ORDER and SHAPE
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_mode_call_order_pins_broker_lookup_chain(
    monkeypatch, disable_risk_guard,
):
    """Pin the exact order: db.broker_connections.find_one →
    _get_user_broker → _get_or_refresh_client → place_order.

    Pins the kwargs ``place_order`` is called with (symbol/qty/side
    lowercase/order_type=market/time_in_force=day).
    """
    call_log: list[str] = []

    fake_db = _FakeDB(broker_conn={"broker_id": "alpaca"})
    monkeypatch.setattr(tbs, "_db", fake_db)

    # Patch the broker-routes helpers (lazy-imported inside live branch)
    fake_get_user_broker = AsyncMock(return_value={"id": "alpaca"})
    fake_client = MagicMock()

    def _spy_place_order(*a, **kw):
        call_log.append("place_order")
        return {"id": "broker-order-42"}
    fake_client.place_order = MagicMock(side_effect=_spy_place_order)

    fake_get_or_refresh_client = AsyncMock(return_value=fake_client)

    async def _spy_get_user_broker(*a, **kw):
        call_log.append("get_user_broker")
        return await fake_get_user_broker(*a, **kw)

    async def _spy_get_or_refresh_client(*a, **kw):
        call_log.append("get_or_refresh_client")
        return await fake_get_or_refresh_client(*a, **kw)

    import routes.broker as routes_broker
    monkeypatch.setattr(routes_broker, "_get_user_broker", _spy_get_user_broker)
    monkeypatch.setattr(routes_broker, "_get_or_refresh_client",
                        _spy_get_or_refresh_client)

    # Wire broker_connections.find_one to also log
    real_find_one = fake_db.broker_connections.find_one

    async def _spy_find_one(*a, **kw):
        call_log.append("broker_connections.find_one")
        return await real_find_one(*a, **kw)
    fake_db.broker_connections.find_one = _spy_find_one

    bot = {"mode": "live", "user_id": "u1", "name": "LiveBot"}
    result = await tbs._execute_bot_trade(
        bot=bot, symbol="AAPL", side="BUY", qty=2, price=100.0,
    )

    # ── ORDER pin ──
    assert call_log == [
        "broker_connections.find_one",
        "get_user_broker",
        "get_or_refresh_client",
        "place_order",
    ], f"broker call order drifted: {call_log}"

    # ── KWARGS pin (case-sensitive on side: must be lowercase) ──
    assert fake_client.place_order.call_args.kwargs == {
        "symbol": "AAPL", "qty": 2, "side": "buy",
        "order_type": "market", "time_in_force": "day",
    }

    # ── SUCCESS RETURN SHAPE pin ──
    assert result == {
        "status": "filled", "broker_order_id": "broker-order-42",
        "symbol": "AAPL", "side": "BUY", "qty": 2,
    }


# ──────────────────────────────────────────────────────────────────────
# 3. Live-mode error paths — pin every short-circuit branch
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_mode_db_none_short_circuits_before_any_broker_call(
    monkeypatch, disable_risk_guard, caplog,
):
    """``_db is None`` returns the DB-unavailable error WITHOUT
    importing routes.broker or doing any other I/O.
    """
    monkeypatch.setattr(tbs, "_db", None)

    # Tripwire: any access to routes.broker should raise
    import routes.broker as routes_broker

    def _explode(*a, **kw):
        raise AssertionError("routes.broker accessed despite _db=None")
    monkeypatch.setattr(routes_broker, "_get_user_broker", _explode)
    monkeypatch.setattr(routes_broker, "_get_or_refresh_client", _explode)

    with caplog.at_level(logging.ERROR):
        result = await tbs._execute_bot_trade(
            bot={"mode": "live", "user_id": "u1"}, symbol="AAPL",
            side="buy", qty=1, price=100.0,
        )
    assert result == {"error": "DB unavailable"}


@pytest.mark.asyncio
async def test_live_mode_no_broker_conn_returns_no_broker_error(
    monkeypatch, disable_risk_guard,
):
    """Missing broker connection returns the canonical error string.

    Pins both the message and the early-return discipline (no
    ``routes.broker`` import attempted).
    """
    fake_db = _FakeDB(broker_conn=None)
    monkeypatch.setattr(tbs, "_db", fake_db)

    import routes.broker as routes_broker

    def _explode(*a, **kw):
        raise AssertionError("routes.broker accessed without broker_conn")
    monkeypatch.setattr(routes_broker, "_get_user_broker", _explode)
    monkeypatch.setattr(routes_broker, "_get_or_refresh_client", _explode)

    result = await tbs._execute_bot_trade(
        bot={"mode": "live", "user_id": "u1"}, symbol="AAPL",
        side="buy", qty=1, price=100.0,
    )
    assert result == {"error": "No broker connected for live trading"}


@pytest.mark.asyncio
async def test_live_mode_falsy_broker_result_returns_rejected_error(
    monkeypatch, disable_risk_guard,
):
    """``place_order`` returning ``None``/``{}``/``False`` maps to
    the canonical ``Broker rejected order`` error — NOT an exception.
    """
    fake_db = _FakeDB(broker_conn={"broker_id": "alpaca"})
    monkeypatch.setattr(tbs, "_db", fake_db)

    fake_client = MagicMock()
    fake_client.place_order = MagicMock(return_value=None)

    import routes.broker as routes_broker
    monkeypatch.setattr(routes_broker, "_get_user_broker",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(routes_broker, "_get_or_refresh_client",
                        AsyncMock(return_value=fake_client))

    result = await tbs._execute_bot_trade(
        bot={"mode": "live", "user_id": "u1"}, symbol="AAPL",
        side="buy", qty=1, price=100.0,
    )
    assert result == {"error": "Broker rejected order"}


@pytest.mark.asyncio
async def test_live_mode_exception_returns_sanitized_error(
    monkeypatch, disable_risk_guard,
):
    """Any exception from broker layer → ``{"error": "Live execution
    failed: ..."}``. Caller never sees the raw exception."""
    fake_db = _FakeDB(broker_conn={"broker_id": "alpaca"})
    monkeypatch.setattr(tbs, "_db", fake_db)

    import routes.broker as routes_broker
    monkeypatch.setattr(
        routes_broker, "_get_user_broker",
        AsyncMock(side_effect=RuntimeError("alpaca-401")),
    )
    monkeypatch.setattr(routes_broker, "_get_or_refresh_client",
                        AsyncMock())

    result = await tbs._execute_bot_trade(
        bot={"mode": "live", "user_id": "u1"}, symbol="AAPL",
        side="buy", qty=1, price=100.0,
    )
    assert "error" in result
    assert result["error"].startswith("Live execution failed:")
    assert "alpaca-401" in result["error"]


# ──────────────────────────────────────────────────────────────────────
# 4. Unknown-mode contract
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_mode_returns_graceful_error_not_exception(
    monkeypatch, disable_risk_guard,
):
    """``mode="weird"`` → graceful error dict. No exception escapes."""
    result = await tbs._execute_bot_trade(
        bot={"mode": "weird", "user_id": "u1"}, symbol="AAPL",
        side="buy", qty=1, price=100.0,
    )
    assert result == {"error": "Unknown bot mode: weird"}


# ──────────────────────────────────────────────────────────────────────
# 5. Risk-guard pre-flight contract
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_risk_guard_runs_BEFORE_broker_call(monkeypatch):
    """The risk guard must execute strictly before any broker
    lookup — pinning that an adjusted qty propagates to ``place_order``.
    """
    seen_order: list[str] = []

    async def _fake_guard(bot, user_id, qty):
        seen_order.append("risk_guard")
        # Halve and report
        return qty / 2, {"risk_reduced": True, "original_qty": qty,
                          "adjusted_qty": qty / 2, "reason": "streak"}
    monkeypatch.setattr(tbs, "_apply_bot_risk_guards", _fake_guard)

    fake_db = _FakeDB(broker_conn={"broker_id": "alpaca"})
    monkeypatch.setattr(tbs, "_db", fake_db)

    real_find_one = fake_db.broker_connections.find_one

    async def _spy_find_one(*a, **kw):
        seen_order.append("broker_connections.find_one")
        return await real_find_one(*a, **kw)
    fake_db.broker_connections.find_one = _spy_find_one

    fake_client = MagicMock()
    fake_client.place_order = MagicMock(return_value={"id": "ok"})
    import routes.broker as routes_broker
    monkeypatch.setattr(routes_broker, "_get_user_broker",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(routes_broker, "_get_or_refresh_client",
                        AsyncMock(return_value=fake_client))

    await tbs._execute_bot_trade(
        bot={"mode": "live", "user_id": "u1"}, symbol="AAPL",
        side="buy", qty=10, price=100.0,
    )

    # Risk-guard must precede the first broker lookup
    assert seen_order[0] == "risk_guard"
    assert "broker_connections.find_one" in seen_order

    # Adjusted qty (10 → 5) must reach place_order
    assert fake_client.place_order.call_args.kwargs["qty"] == 5


@pytest.mark.asyncio
async def test_risk_guard_db_none_fails_open(monkeypatch):
    """``_db is None`` → guard returns (qty, ctx) unchanged. No raise."""
    monkeypatch.setattr(tbs, "_db", None)
    qty, ctx = await tbs._apply_bot_risk_guards(
        bot={"name": "B"}, user_id="u1", qty=4.0,
    )
    assert qty == 4.0
    assert ctx == {"risk_reduced": False, "original_qty": 4.0,
                   "adjusted_qty": 4.0, "reason": None}


@pytest.mark.asyncio
async def test_risk_guard_reduces_qty_when_circuit_breaker_trips(monkeypatch):
    """``risk_ctx.risk_reduced=True`` halves qty (×reduction_factor)
    with floor 1.0. Returns enriched ctx including streak/drawdown.
    """
    fake_db = _FakeDB()
    monkeypatch.setattr(tbs, "_db", fake_db)

    import routes.risk_calculator as rc
    monkeypatch.setattr(rc, "_get_account_value", AsyncMock(return_value=1000))
    monkeypatch.setattr(rc, "_compute_risk_context", AsyncMock(return_value={
        "risk_reduced": True, "reduction_factor": 0.5,
        "reason": "losing_streak", "losing_streak": 4,
        "current_drawdown": 0.12,
    }))
    # Stub the audit log so we don't hit Mongo
    import services.rejection_log as rj
    monkeypatch.setattr(rj, "log_rejected", AsyncMock())

    qty, ctx = await tbs._apply_bot_risk_guards(
        bot={"name": "B", "symbol": "AAPL", "type": "signal", "mode": "paper"},
        user_id="u1", qty=10.0,
    )
    assert qty == 5.0
    assert ctx["risk_reduced"] is True
    assert ctx["original_qty"] == 10.0
    assert ctx["adjusted_qty"] == 5.0
    assert ctx["reason"] == "losing_streak"
    assert ctx["losing_streak"] == 4
    assert ctx["current_drawdown"] == 0.12


@pytest.mark.asyncio
async def test_risk_guard_floor_protects_against_zero_qty(monkeypatch):
    """Reduction must NEVER round below 1.0 (kept by ``max(1.0, ...)``)."""
    fake_db = _FakeDB()
    monkeypatch.setattr(tbs, "_db", fake_db)

    import routes.risk_calculator as rc
    monkeypatch.setattr(rc, "_get_account_value", AsyncMock(return_value=1000))
    monkeypatch.setattr(rc, "_compute_risk_context", AsyncMock(return_value={
        "risk_reduced": True, "reduction_factor": 0.01,
        "reason": "deep_drawdown",
    }))
    import services.rejection_log as rj
    monkeypatch.setattr(rj, "log_rejected", AsyncMock())

    qty, _ = await tbs._apply_bot_risk_guards(
        bot={"name": "B"}, user_id="u1", qty=2.0,
    )
    assert qty == 1.0


@pytest.mark.asyncio
async def test_risk_guard_exception_fails_open(monkeypatch):
    """Any exception inside the guard → original qty + base ctx
    returned. Bot keeps trading (NEVER frozen by Mongo hiccups).
    """
    fake_db = _FakeDB()
    monkeypatch.setattr(tbs, "_db", fake_db)

    import routes.risk_calculator as rc
    monkeypatch.setattr(
        rc, "_get_account_value",
        AsyncMock(side_effect=RuntimeError("mongo-down")),
    )

    qty, ctx = await tbs._apply_bot_risk_guards(
        bot={"name": "B"}, user_id="u1", qty=7.0,
    )
    # Fail-open: original qty preserved
    assert qty == 7.0
    assert ctx["risk_reduced"] is False
    assert ctx["original_qty"] == 7.0
    assert ctx["adjusted_qty"] == 7.0
    assert ctx["reason"] is None


# ──────────────────────────────────────────────────────────────────────
# 6. Place-order is wrapped in asyncio.to_thread (sync-call discipline)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_place_order_wrapped_in_to_thread(
    monkeypatch, disable_risk_guard,
):
    """``client.place_order`` is sync; the live branch MUST wrap it
    in ``asyncio.to_thread`` so it doesn't block the event loop.

    This pins the wrapper — if 4E ever changes the call to a direct
    ``await client.place_order(...)`` (which would silently break
    sync clients), this test fails.
    """
    fake_db = _FakeDB(broker_conn={"broker_id": "alpaca"})
    monkeypatch.setattr(tbs, "_db", fake_db)

    import routes.broker as routes_broker
    fake_client = MagicMock()
    fake_client.place_order = MagicMock(return_value={"id": "ok"})
    monkeypatch.setattr(routes_broker, "_get_user_broker",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(routes_broker, "_get_or_refresh_client",
                        AsyncMock(return_value=fake_client))

    # Spy on asyncio.to_thread
    import asyncio
    real_to_thread = asyncio.to_thread
    to_thread_calls: list[tuple] = []

    async def _spy_to_thread(fn, *args, **kwargs):
        to_thread_calls.append((fn, args, kwargs))
        return await real_to_thread(fn, *args, **kwargs)
    monkeypatch.setattr(asyncio, "to_thread", _spy_to_thread)

    await tbs._execute_bot_trade(
        bot={"mode": "live", "user_id": "u1"}, symbol="AAPL",
        side="buy", qty=1, price=100.0,
    )

    # to_thread invoked exactly once with place_order as the target
    assert len(to_thread_calls) == 1
    fn, _, kwargs = to_thread_calls[0]
    assert fn is fake_client.place_order
    assert kwargs == {"symbol": "AAPL", "qty": 1, "side": "buy",
                      "order_type": "market", "time_in_force": "day"}
