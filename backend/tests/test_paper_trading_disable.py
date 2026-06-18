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
    """Hardcoded seal — paper trading is PERMANENTLY retired.
    No env-var path can re-enable it. The autouse conftest
    fixture sets ``PAPER_TRADING_ENABLED=true`` for legacy
    pipeline tests, but the seal ignores it."""
    import asyncio

    # Even with the autouse fixture setting truthy, the result is
    # the sealed reason — not the legacy "paper_trading_disabled".
    out = asyncio.run(paper_trader.run_crypto_symbol(
        db=object(),
        symbol="BTC",
        bars=[100.0] * 50,
        quote_provider=None,
    ))
    assert out["skipped"] is True
    assert out["reason"] == "paper_trading_retired"


def test_crypto_run_skips_for_unknown_env_value(monkeypatch):
    """Even with garbage env values, the seal is unconditional."""
    import asyncio
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "garbage")
    out = asyncio.run(paper_trader.run_crypto_symbol(
        db=object(), symbol="BTC", bars=[100.0] * 50, quote_provider=None,
    ))
    assert out["reason"] == "paper_trading_retired"


def test_crypto_run_refuses_even_when_env_truthy(monkeypatch):
    """The seal is hardcoded — setting the env var to true does
    NOT re-enable paper trading. Pin so a future operator-or-AI
    cannot 'fix' the seal by re-adding env handling."""
    import asyncio
    for val in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("PAPER_TRADING_ENABLED", val)
        out = asyncio.run(paper_trader.run_crypto_symbol(
            db=object(), symbol="BTC", bars=[100.0] * 50, quote_provider=None,
        ))
        assert out["reason"] == "paper_trading_retired", (
            f"env={val!r} should NOT re-enable paper trading"
        )


@pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
def test_env_truthy_does_not_unseal(monkeypatch, val):
    """Pin: setting the env var truthy MUST NOT un-seal paper.
    This is the entire point of the 2026-06-16 hardcoded seal."""
    import asyncio
    monkeypatch.setenv("PAPER_TRADING_ENABLED", val)
    out = asyncio.run(paper_trader.run_crypto_symbol(
        db=None, symbol="BTC", bars=[100.0] * 50, quote_provider=None,
    ))
    assert out["reason"] == "paper_trading_retired"


# ── Source-level pins ────────────────────────────────────────────────


def test_crypto_paper_returns_sealed_reason_first():
    """Pin: the sealed early-return is the FIRST line of executable
    code in run_crypto_symbol. Regression would silently re-enable
    paper writes."""
    src = inspect.getsource(paper_trader.run_crypto_symbol)
    sealed_pos = src.find("paper_trading_retired")
    db_check_pos = src.find("db_missing")
    assert sealed_pos > 0 and db_check_pos > 0
    assert sealed_pos < db_check_pos, (
        "paper_trading_retired return MUST appear before any other "
        "decision (regression of 2026-06-16 toxic-spike seal)."
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
