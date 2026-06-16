"""Alpaca trading disable — tripwire (2026-06-12).

Operator directive: drop Alpaca. Not use it for trading anything.

Doctrine pins:
    1. ``BROKER_ALPACA_TRADING_ENABLED`` unset → ``maybe_execute_live``
       returns None WITHOUT touching the Tier 3 opt-in branch.
    2. Symmetric: when explicitly enabled, the gate falls through to
       the rest of the legacy logic (we don't break re-arm).
    3. The Alpaca QUOTES path (``services/alpaca_equity_quotes.py``)
       is untouched — only order placement is gated.
"""
from __future__ import annotations

import inspect

import pytest

import services.ml_alpaca_broker as alpaca_broker
import services.alpaca_equity_quotes as alpaca_quotes


def test_alpaca_trading_gate_function_exists():
    assert hasattr(alpaca_broker, "_alpaca_trading_enabled")


def test_alpaca_trading_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BROKER_ALPACA_TRADING_ENABLED", raising=False)
    assert alpaca_broker._alpaca_trading_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
def test_alpaca_trading_enabled_truthy(monkeypatch, val):
    monkeypatch.setenv("BROKER_ALPACA_TRADING_ENABLED", val)
    assert alpaca_broker._alpaca_trading_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "garbage"])
def test_alpaca_trading_disabled_non_truthy(monkeypatch, val):
    monkeypatch.setenv("BROKER_ALPACA_TRADING_ENABLED", val)
    assert alpaca_broker._alpaca_trading_enabled() is False


def test_maybe_execute_live_checks_gate_before_opt_in():
    """Source-level pin: the new gate MUST run before _LIVE_OPT_IN
    (otherwise re-arming `RISEDUAL_LIVE_EXECUTION` would bypass the
    operator's drop-Alpaca directive)."""
    src = inspect.getsource(alpaca_broker.maybe_execute_live)
    gate_pos = src.find("_alpaca_trading_enabled")
    opt_in_pos = src.find("_LIVE_OPT_IN")
    assert gate_pos > 0
    assert opt_in_pos > 0
    assert gate_pos < opt_in_pos, (
        "BROKER_ALPACA_TRADING_ENABLED gate must run BEFORE the "
        "RISEDUAL_LIVE_EXECUTION opt-in check (regression of the "
        "2026-06-12 operator directive to drop Alpaca)."
    )


def test_alpaca_quotes_module_still_importable():
    """Sanity pin: the disable is trading-only. Quotes service must
    remain intact so equity-data pipelines that depend on it keep
    working."""
    assert hasattr(alpaca_quotes, "__name__")


@pytest.mark.asyncio
async def test_maybe_execute_live_no_op_when_disabled(monkeypatch):
    """End-to-end: with the gate off, the function returns None
    without ever calling Alpaca's REST API. The fact that we get
    a None return without any Alpaca credentials being read or
    any HTTP being attempted is the contract."""
    monkeypatch.delenv("BROKER_ALPACA_TRADING_ENABLED", raising=False)
    # Build minimal stub args; everything past the gate would explode
    # without proper Alpaca config — so a clean None return proves
    # the gate fired first.
    out = await alpaca_broker.maybe_execute_live(
        ticker="AAPL",
        signal=None,
        snapshot=None,
        regime="bull",
        db=None,
    )
    assert out is None
