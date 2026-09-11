"""MooMoo bridge health + read-only smoke test.

Locked-in behaviour:

* Readiness ladder is monotonic: DISCONNECTED → CONNECTED → DATA_READY
  → RESEARCH_READY → EXECUTION_READY. Every rung requires the prior.
* EXECUTION_READY requires BOTH ``MOOMOO_LIVE_ENABLED=1`` AND
  ``MOOMOO_EXECUTION_READY=1``. Either one off holds us at
  RESEARCH_READY.
* Smoke test never submits an order. Failure at any stage stops the
  probe and reports the failing stage.
* Health probes are best-effort — a raising SDK downgrades to a
  failed-stage report, not an exception.
"""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from services import moomoo_bridge_health as bridge
from services.moomoo_bridge_health import ReadinessState


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MOOMOO_OPEND_HOST", "127.0.0.1")
    monkeypatch.setenv("MOOMOO_OPEND_PORT", "11111")
    monkeypatch.setenv("MOOMOO_CANARY_SYMBOL", "SPY")
    monkeypatch.setenv("MOOMOO_LIVE_ENABLED", "0")
    monkeypatch.setenv("MOOMOO_EXECUTION_READY", "0")
    yield


# ── Ladder monotonicity ────────────────────────────────────────────

def test_disconnected_when_tcp_unreachable(monkeypatch):
    monkeypatch.setattr(bridge, "_probe_tcp",
                        lambda *a, **kw: (False, None, "tcp_timeout:mock"))
    h = bridge.compute_health()
    assert h.state == ReadinessState.DISCONNECTED
    assert h.opend_reachable is False
    assert "tcp_timeout" in " ".join(h.errors)


def test_connected_when_tcp_ok_but_quote_fails(monkeypatch):
    monkeypatch.setattr(bridge, "_probe_tcp", lambda *a, **kw: (True, 1.2, None))
    monkeypatch.setattr(bridge, "_probe_quote",
                        lambda sym: (False, None, "quote_snapshot_none"))
    h = bridge.compute_health()
    assert h.state == ReadinessState.CONNECTED
    assert h.opend_reachable is True
    assert h.market_data_ok is False


def test_data_ready_when_quote_ok_but_account_fails(monkeypatch):
    monkeypatch.setattr(bridge, "_probe_tcp", lambda *a, **kw: (True, 1.0, None))
    monkeypatch.setattr(bridge, "_probe_quote", lambda sym: (
        True, {"last": 500.0, "bid": 499.9, "ask": 500.1,
               "volume": 1000, "ts": "", "latency_ms": 12.0}, None,
    ))
    monkeypatch.setattr(bridge, "_probe_account",
                        lambda: (False, None, "account_info_none"))
    h = bridge.compute_health()
    assert h.state == ReadinessState.DATA_READY
    assert h.market_data_ok is True
    assert h.canary_last_price == 500.0
    assert h.account_ok is False


def test_research_ready_when_account_ok_but_execution_gate_off(monkeypatch):
    monkeypatch.setattr(bridge, "_probe_tcp", lambda *a, **kw: (True, 1.0, None))
    monkeypatch.setattr(bridge, "_probe_quote", lambda sym: (
        True, {"last": 500.0, "bid": 499.9, "ask": 500.1,
               "volume": 1000, "ts": "", "latency_ms": 12.0}, None,
    ))
    monkeypatch.setattr(bridge, "_probe_account", lambda: (
        True, {"power": 10000.0, "trd_env": "REAL"}, None,
    ))
    h = bridge.compute_health()
    # Both gates are off → held at RESEARCH_READY.
    assert h.state == ReadinessState.RESEARCH_READY
    assert h.trade_ctx_ok is True
    assert h.buying_power_usd == 10000.0
    assert any("execution_gate_off" in w for w in h.warnings)


def test_execution_ready_needs_both_gates(monkeypatch):
    monkeypatch.setattr(bridge, "_probe_tcp", lambda *a, **kw: (True, 1.0, None))
    monkeypatch.setattr(bridge, "_probe_quote", lambda sym: (
        True, {"last": 500.0, "bid": 499.9, "ask": 500.1,
               "volume": 1000, "ts": "", "latency_ms": 12.0}, None,
    ))
    monkeypatch.setattr(bridge, "_probe_account", lambda: (
        True, {"power": 10000.0, "trd_env": "REAL"}, None,
    ))

    # Only LIVE_ENABLED on → still RESEARCH_READY
    monkeypatch.setenv("MOOMOO_LIVE_ENABLED", "1")
    monkeypatch.setenv("MOOMOO_EXECUTION_READY", "0")
    assert bridge.compute_health().state == ReadinessState.RESEARCH_READY

    # Only EXECUTION_READY on → still RESEARCH_READY
    monkeypatch.setenv("MOOMOO_LIVE_ENABLED", "0")
    monkeypatch.setenv("MOOMOO_EXECUTION_READY", "1")
    assert bridge.compute_health().state == ReadinessState.RESEARCH_READY

    # Both on → EXECUTION_READY
    monkeypatch.setenv("MOOMOO_LIVE_ENABLED", "1")
    monkeypatch.setenv("MOOMOO_EXECUTION_READY", "1")
    assert bridge.compute_health().state == ReadinessState.EXECUTION_READY


# ── TCP probe safety ──────────────────────────────────────────────

def test_tcp_probe_reports_error_without_raising(monkeypatch):
    # Point at a port we definitely can't reach.
    ok, latency, err = bridge._probe_tcp("127.0.0.1", 1, timeout=0.2)
    assert ok is False
    assert latency is None
    assert err is not None


def test_tcp_probe_returns_latency_on_success():
    # Bind an ephemeral port, then probe it.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    _, port = srv.getsockname()
    try:
        ok, latency, err = bridge._probe_tcp("127.0.0.1", port, timeout=0.5)
        assert ok is True
        assert isinstance(latency, float)
        assert err is None
    finally:
        srv.close()


# ── Health snapshot shape ──────────────────────────────────────────

def test_health_as_dict_shape_at_disconnected(monkeypatch):
    monkeypatch.setattr(bridge, "_probe_tcp",
                        lambda *a, **kw: (False, None, "tcp_timeout"))
    d = bridge.compute_health().as_dict()
    assert d["state"] == ReadinessState.DISCONNECTED
    assert set(d.keys()) == {
        "state", "checked_at_ns", "opend", "market_data", "trade", "gates",
        "warnings", "errors",
    }
    assert set(d["opend"].keys()) == {"host", "port", "reachable", "tcp_latency_ms"}
    assert set(d["gates"].keys()) == {"live_enabled", "execution_ready_flag"}


# ── Smoke test — never submits ─────────────────────────────────────

def test_smoke_test_stops_at_tunnel_failure(monkeypatch):
    monkeypatch.setattr(bridge, "_probe_tcp",
                        lambda *a, **kw: (False, None, "tcp_timeout:mock"))
    result = bridge.run_smoke_test(symbol="SPY")
    assert result["tunnel"]["ok"] is False
    assert result["overall"]["ok"] is False
    assert result["overall"]["failed_at"] == "tunnel"


def test_smoke_test_stops_at_market_data(monkeypatch):
    monkeypatch.setattr(bridge, "_probe_tcp", lambda *a, **kw: (True, 1.0, None))
    monkeypatch.setattr(bridge, "_probe_quote",
                        lambda sym: (False, None, "quote_snapshot_none"))
    result = bridge.run_smoke_test(symbol="SPY")
    assert result["tunnel"]["ok"] is True
    assert result["market_data"]["ok"] is False
    assert result["overall"]["failed_at"] == "market_data"


def test_smoke_test_reaches_research_snapshot(monkeypatch):
    """End-to-end smoke test with all rungs mocked green."""
    monkeypatch.setattr(bridge, "_probe_tcp", lambda *a, **kw: (True, 1.0, None))
    monkeypatch.setattr(bridge, "_probe_quote", lambda sym: (
        True, {"last": 500.0, "bid": 499.9, "ask": 500.1,
               "volume": 1000, "ts": "", "latency_ms": 12.0}, None,
    ))
    monkeypatch.setattr(bridge, "_probe_account", lambda: (
        True, {"power": 10000.0, "trd_env": "REAL"}, None,
    ))
    # Short-circuit the research snapshot so we don't actually hit
    # market_data_pool during unit tests.
    async def _fake_research(*, symbol, signal_price):
        from services.alpha_broker_research import BrokerResearchSnapshot
        import time as _t
        return BrokerResearchSnapshot(
            symbol=symbol, broker="moomoo", fetched_at_ns=_t.time_ns(),
            signal_price=signal_price, current_price=signal_price,
            drift_bps=0.0, broker_quote_age_seconds=0.5,
            bid=499.9, ask=500.1, mid=500.0, spread_bps=4.0,
        )
    import services.alpha_broker_research as _r
    monkeypatch.setattr(_r, "perform_research", _fake_research)

    result = bridge.run_smoke_test(symbol="SPY")
    assert result["overall"]["ok"] is True
    assert result["research_snapshot"]["ok"] is True
    assert result["research_snapshot"]["broker"] == "moomoo"
    assert result["research_snapshot"]["current_price"] == 500.0


def test_smoke_test_never_submits(monkeypatch):
    """Belt-and-suspenders: even if a caller monkey-patches the
    adapter to raise on submit, the smoke test path never touches it."""
    monkeypatch.setattr(bridge, "_probe_tcp", lambda *a, **kw: (True, 1.0, None))
    monkeypatch.setattr(bridge, "_probe_quote", lambda sym: (
        True, {"last": 500.0, "bid": 499.9, "ask": 500.1,
               "volume": 1000, "ts": "", "latency_ms": 12.0}, None,
    ))
    monkeypatch.setattr(bridge, "_probe_account", lambda: (
        True, {"power": 10000.0, "trd_env": "REAL"}, None,
    ))
    # Short-circuit research to avoid any real broker/vendor I/O in
    # the unit test environment.
    async def _fake_research(*, symbol, signal_price):
        from services.alpha_broker_research import BrokerResearchSnapshot
        import time as _t
        return BrokerResearchSnapshot(
            symbol=symbol, broker="moomoo", fetched_at_ns=_t.time_ns(),
        )
    import services.alpha_broker_research as _r
    monkeypatch.setattr(_r, "perform_research", _fake_research)
    from services import moomoo_broker_adapter

    def _fail_if_called(*a, **kw):
        raise AssertionError("smoke test must NEVER submit an order")

    monkeypatch.setattr(moomoo_broker_adapter, "submit_equity", _fail_if_called)
    # Should complete without triggering the AssertionError above.
    result = bridge.run_smoke_test(symbol="SPY")
    assert "research_snapshot" in result


# ── Adapter probes handle SDK exceptions ──────────────────────────

def test_quote_probe_returns_error_on_exception(monkeypatch):
    from services import moomoo_market_data_adapter
    def _boom(sym):
        raise RuntimeError("openD dead")
    monkeypatch.setattr(moomoo_market_data_adapter, "snapshot_quote", _boom)
    ok, quote, err = bridge._probe_quote("SPY")
    assert ok is False
    assert quote is None
    assert err.startswith("quote_exception")


def test_account_probe_returns_error_on_exception(monkeypatch):
    from services import moomoo_broker_adapter
    def _boom():
        raise RuntimeError("openD dead")
    monkeypatch.setattr(moomoo_broker_adapter, "account_info", _boom)
    ok, acct, err = bridge._probe_account()
    assert ok is False
    assert acct is None
    assert err.startswith("account_exception")
