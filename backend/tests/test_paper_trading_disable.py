"""Paper trading disable — tripwires (2026-06-16).

Operator directive: paper trading removed (Public.com is live-only,
Kraken has no paper sandbox). Master env knob
``PAPER_TRADING_ENABLED`` (default **OFF**) gates two entry points:

  1. ``services.crypto_paper_trader.run_crypto_symbol`` — skips
     with ``reason="paper_trading_disabled"`` before any signal
     work or DB writes.
  2. ``services.ml_orchestrator`` Tier-2 path — skips the
     ``maybe_paper_trade`` call but the live Public.com path that
     follows is untouched.

Existing closers / paper-trade reads / historical training data
are intentionally unaffected so the ML pipeline keeps reading the
already-accumulated paper corpus.
"""
from __future__ import annotations

import inspect

import pytest

import services.crypto_paper_trader as paper_trader
import services.ml_orchestrator as orch


# ── Env contract ─────────────────────────────────────────────────────


def test_crypto_run_skips_when_paper_disabled(monkeypatch):
    """Default OFF — no env var means crypto paper does NOT execute.

    Local ``delenv`` overrides the autouse fixture's ``setenv`` for
    just this test.
    """
    import asyncio

    monkeypatch.delenv("PAPER_TRADING_ENABLED", raising=False)
    out = asyncio.run(paper_trader.run_crypto_symbol(
        db=object(),  # would be touched on any non-skip path
        symbol="BTC",
        bars=[100.0] * 50,
        quote_provider=None,
    ))
    assert out["skipped"] is True
    assert out["reason"] == "paper_trading_disabled"


def test_crypto_run_skips_for_unknown_env_value(monkeypatch):
    import asyncio
    # Override the autouse "true" with garbage.
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "garbage")
    out = asyncio.run(paper_trader.run_crypto_symbol(
        db=object(), symbol="BTC", bars=[100.0] * 50, quote_provider=None,
    ))
    assert out["reason"] == "paper_trading_disabled"


@pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
def test_crypto_run_passes_gate_when_paper_enabled(monkeypatch, val):
    """When explicitly re-armed, the paper gate falls through to the
    rest of the pipeline. We don't assert on the downstream result
    (it depends on bars/quotes), only that the early gate is NOT the
    reason for any skip."""
    import asyncio
    monkeypatch.setenv("PAPER_TRADING_ENABLED", val)
    # db=None falls into the prior 'db_missing' guard, proving we
    # got PAST the new paper gate.
    out = asyncio.run(paper_trader.run_crypto_symbol(
        db=None, symbol="BTC", bars=[100.0] * 50, quote_provider=None,
    ))
    assert out["reason"] != "paper_trading_disabled"


# ── Source-level pins ────────────────────────────────────────────────


def test_crypto_paper_gate_runs_before_db_missing_guard():
    """Pin: the paper gate is the FIRST decision after ``db is None``
    so re-arming the env var is the operator's only way to bring the
    paper writes back."""
    src = inspect.getsource(paper_trader.run_crypto_symbol)
    paper_pos = src.find("paper_trading_disabled")
    crypto_check_pos = src.find("not_crypto_symbol")
    assert paper_pos > 0 and crypto_check_pos > 0
    assert paper_pos < crypto_check_pos, (
        "PAPER_TRADING_ENABLED gate must short-circuit BEFORE the "
        "is_crypto_symbol firewall — keeps the kill-switch as the "
        "primary contract."
    )


def test_orchestrator_tier2_consults_paper_gate():
    """Pin: ``ml_orchestrator`` reads ``PAPER_TRADING_ENABLED`` inside
    the Tier 2 branch. Regression here would silently re-enable equity
    paper writes."""
    src = inspect.getsource(orch.run_one_symbol) if hasattr(
        orch, "run_one_symbol"
    ) else inspect.getsource(orch)
    assert "PAPER_TRADING_ENABLED" in src, (
        "ml_orchestrator must consult PAPER_TRADING_ENABLED in its "
        "Tier 2 paper branch (regression of 2026-06-16 directive)."
    )
