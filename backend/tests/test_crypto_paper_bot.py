"""Tests for the v3 isolated crypto paper-trading BOT lane.

Architecture this file pins down
--------------------------------
* The bot writes EXCLUSIVELY to ``db.crypto_paper_trades`` —
  never the legacy ``paper_trades`` collection.
* Quotes go through the injected ``quote_provider`` (production:
  ``services.crypto_quotes.get_crypto_quote``) — never the equity
  ``get_quote`` path.
* History goes through the injected ``history_provider`` —
  isolated from ``price_provider.get_daily_history``.
* Non-crypto symbols (AAPL, SPY) are filtered at the boundary.
* Confidence-scaled position sizing — high-conviction fills carry
  more notional, capped at $1000.
* Defensive SL/TP defaults applied to every fill.
* Every Auditor verdict (CONFIRM, VETO, HOLD) lands in
  ``crypto_signal_audit_log`` so the calibration tile sees the
  full denominator.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.crypto_paper_trader import (
    CRYPTO_SYMBOLS,
    BASE_CRYPTO_NOTIONAL,
    MAX_CRYPTO_NOTIONAL,
    MIN_CRYPTO_CONFIDENCE,
    build_stop_take_profit,
    compute_crypto_position_size,
    infer_crypto_regime,
    infer_failure_context,
    is_crypto_symbol,
    run_crypto_paper_bot,
    run_crypto_symbol,
)


# ── Symbol guard ──────────────────────────────────────────────────────────────


def test_is_crypto_symbol_accepts_bare_pair_and_dash_forms():
    assert is_crypto_symbol("BTC") is True
    assert is_crypto_symbol("BTC/USD") is True
    assert is_crypto_symbol("btc-usd") is True
    assert is_crypto_symbol("ETH") is True


def test_is_crypto_symbol_rejects_equities_and_garbage():
    assert is_crypto_symbol("AAPL") is False
    assert is_crypto_symbol("SPY") is False
    assert is_crypto_symbol(None) is False
    assert is_crypto_symbol("") is False


def test_default_universe_is_btc_eth_sol():
    assert CRYPTO_SYMBOLS == ["BTC", "ETH", "SOL"]


# ── Position sizing ───────────────────────────────────────────────────────────


def test_size_zero_below_floor():
    assert compute_crypto_position_size(0.5) == 0.0
    assert compute_crypto_position_size(0.59) == 0.0


def test_size_at_floor_returns_base():
    assert compute_crypto_position_size(MIN_CRYPTO_CONFIDENCE) == BASE_CRYPTO_NOTIONAL


def test_size_scales_with_confidence():
    s_low = compute_crypto_position_size(0.65)
    s_high = compute_crypto_position_size(0.90)
    assert s_low > 0
    assert s_high > s_low


def test_size_capped_at_max():
    # Even confidence of 0.99 cannot push notional above MAX
    assert compute_crypto_position_size(0.99) <= MAX_CRYPTO_NOTIONAL


# ── SL/TP defaults ────────────────────────────────────────────────────────────


def test_stops_long_2pct_4pct():
    stops = build_stop_take_profit(100.0, "LONG")
    assert stops["stop_loss"] == 98.0
    assert stops["take_profit"] == 104.0


def test_stops_short_inverted():
    stops = build_stop_take_profit(100.0, "SHORT")
    assert stops["stop_loss"] == 102.0
    assert stops["take_profit"] == 96.0


# ── Regime + failure-context taggers ──────────────────────────────────────────


def test_infer_regime_parabolic_takes_priority():
    sig = {
        "direction": "LONG",
        "strategist": {"indicators": {"rsi": 75, "momentum_5b": 0.10}},
    }
    assert infer_crypto_regime(sig) == "parabolic"


def test_infer_regime_overbought_when_rsi_high():
    sig = {
        "direction": "LONG",
        "strategist": {"indicators": {"rsi": 75, "momentum_5b": 0.02}},
    }
    assert infer_crypto_regime(sig) == "overbought"


def test_infer_regime_trend_up_default_for_long():
    sig = {
        "direction": "LONG",
        "strategist": {"indicators": {"rsi": 60, "momentum_5b": 0.02}},
    }
    assert infer_crypto_regime(sig) == "trend_up"


def test_failure_context_likely_extreme_rsi():
    sig = {
        "strategist": {"indicators": {"rsi": 75, "momentum_5b": 0.02}},
    }
    fc = infer_failure_context(sig)
    assert fc["likely_failure_code"] == "EXTREME_RSI_FAILURE"


def test_failure_context_likely_parabolic():
    sig = {
        "strategist": {"indicators": {"rsi": 60, "momentum_5b": 0.10}},
    }
    fc = infer_failure_context(sig)
    assert fc["likely_failure_code"] == "PARABOLIC_EXHAUSTION"


def test_failure_context_likely_none_for_clean_signal():
    sig = {
        "strategist": {"indicators": {"rsi": 60, "momentum_5b": 0.02}},
    }
    fc = infer_failure_context(sig)
    assert fc["likely_failure_code"] is None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _moderate_uptrend(n: int = 60, base: float = 70000.0,
                      drift: float = 200.0) -> list[float]:
    """Synthetic uptrend with realistic pullback noise so RSI lands
    in the 55-65 range. Pattern: 2 bars up, 1 bar down."""
    series = [base]
    for i in range(1, n):
        if i % 3 == 2:
            series.append(series[-1] - drift)
        else:
            series.append(series[-1] + drift)
    return series


class _FakeDB:
    """Async-iter capable Motor stub. Routes every collection through
    ``__getattr__`` so any new collection access in the bot's code
    path doesn't surprise the test."""

    def __init__(self):
        self.crypto_paper_trades = AsyncMock()
        self.crypto_paper_trades.insert_one = self._insert
        self.paper_trades = AsyncMock()  # MUST stay untouched
        self.crypto_signal_audit_log = AsyncMock()
        self.crypto_signal_audit_log.insert_one = self._audit_insert
        self.crypto_model_adaptations = AsyncMock()
        self.crypto_model_adaptations.find = self._empty_cursor
        self.writes: list[dict] = []
        self.audit_writes: list[dict] = []

    def __getitem__(self, key):
        return getattr(self, key)

    def _empty_cursor(self, _query):
        class _C:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        return _C()

    async def _insert(self, doc):
        self.writes.append(dict(doc))

    async def _audit_insert(self, doc):
        self.audit_writes.append(dict(doc))


# ── End-to-end run_crypto_symbol ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_symbol_writes_to_crypto_collection_only():
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    bars = _moderate_uptrend(60)
    result = await run_crypto_symbol(db, "BTC", bars, quote)

    assert result["opened"] is True
    assert result["direction"] == "LONG"
    assert result["size_usd"] >= BASE_CRYPTO_NOTIONAL
    assert result["stop_loss"] < 70000.0  # LONG stop below entry
    assert result["take_profit"] > 70000.0  # LONG target above entry

    # Architectural firewall — paper_trades MUST stay untouched
    db.paper_trades.insert_one.assert_not_called()

    # The crypto collection MUST have one fill
    assert len(db.writes) == 1
    doc = db.writes[0]
    assert doc["asset_class"] == "crypto"
    assert doc["symbol"] == "BTC"
    assert doc["pair"] == "BTC/USD"
    assert doc["source"] == "crypto_paper_bot"
    assert doc["metadata"]["lane"] == "crypto"
    assert doc["metadata"]["bot_version"] == "crypto_v3"
    assert "agent_agreement" in doc
    assert doc["agent_agreement"]["strategist_direction"] == "LONG"
    assert doc["agent_agreement"]["auditor_verdict"] == "CONFIRM"
    assert doc["opened_day"]  # YYYY-MM-DD string


@pytest.mark.asyncio
async def test_run_symbol_rejects_non_crypto():
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 200.0}

    result = await run_crypto_symbol(db, "AAPL", _moderate_uptrend(60), quote)

    assert result["skipped"] is True
    assert result["reason"] == "not_crypto_symbol"
    assert len(db.writes) == 0


@pytest.mark.asyncio
async def test_run_symbol_rejects_insufficient_bars():
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    result = await run_crypto_symbol(db, "BTC", [70000.0] * 10, quote)
    assert result["skipped"] is True
    assert result["reason"] == "insufficient_bars"


@pytest.mark.asyncio
async def test_run_symbol_skips_on_zero_quote():
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 0.0}

    result = await run_crypto_symbol(db, "BTC", _moderate_uptrend(60), quote)
    assert result["skipped"] is True
    assert result["reason"] == "quote_unavailable"
    assert len(db.writes) == 0


@pytest.mark.asyncio
async def test_run_symbol_skips_on_strategist_hold():
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    flat_bars = [70000.0] * 60
    result = await run_crypto_symbol(db, "BTC", flat_bars, quote)
    assert result["skipped"] is True
    assert "strategist_hold" in (result["reason"] or "")
    assert len(db.writes) == 0
    # Audit log still got the HOLD decision (denominator hygiene)
    assert len(db.audit_writes) == 1


@pytest.mark.asyncio
async def test_run_symbol_no_op_on_missing_db():
    async def quote(_sym):
        return {"price": 70000.0}

    result = await run_crypto_symbol(None, "BTC", _moderate_uptrend(60), quote)
    assert result["skipped"] is True
    assert result["reason"] == "db_missing"


# ── Multi-symbol runner ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_bot_returns_opened_skipped_errors_summary():
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    async def history(_sym):
        return _moderate_uptrend(60)

    out = await run_crypto_paper_bot(
        db, quote_provider=quote, history_provider=history,
        symbols=["BTC", "ETH"],
    )

    assert out["opened_count"] == 2
    assert out["skipped_count"] == 0
    assert out["error_count"] == 0
    assert len(db.writes) == 2
    db.paper_trades.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_run_bot_filters_non_crypto_symbols():
    db = _FakeDB()
    quote_calls: list[str] = []

    async def quote(symbol):
        quote_calls.append(symbol)
        return {"price": 70000.0}

    async def history(_sym):
        return _moderate_uptrend(60)

    out = await run_crypto_paper_bot(
        db, quote_provider=quote, history_provider=history,
        symbols=["BTC", "AAPL", "SPY", "ETH"],
    )

    assert out["opened_count"] == 2
    assert out["skipped_count"] == 2
    skipped_symbols = {r["symbol"] for r in out["skipped"]}
    assert skipped_symbols == {"AAPL", "SPY"}
    # Equity tickers never trigger a quote call (firewall is upstream)
    assert "AAPL" not in quote_calls
    assert "SPY" not in quote_calls


@pytest.mark.asyncio
async def test_run_bot_default_universe_is_btc_eth_sol():
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    async def history(_sym):
        return _moderate_uptrend(60)

    out = await run_crypto_paper_bot(
        db, quote_provider=quote, history_provider=history,
    )
    opened_symbols = {r["symbol"] for r in out["opened"]}
    assert opened_symbols == {"BTC", "ETH", "SOL"}


@pytest.mark.asyncio
async def test_run_bot_continues_after_history_fetch_error():
    """A single symbol's history fetch failure must not crash the
    whole tick — it gets captured in ``errors``."""
    db = _FakeDB()
    call = 0

    async def quote(_sym):
        return {"price": 70000.0}

    async def history(symbol):
        nonlocal call
        call += 1
        if symbol == "BTC":
            raise RuntimeError("yfinance offline")
        return _moderate_uptrend(60)

    out = await run_crypto_paper_bot(
        db, quote_provider=quote, history_provider=history,
        symbols=["BTC", "ETH"],
    )

    assert out["error_count"] == 1
    assert out["errors"][0]["symbol"] == "BTC"
    assert out["opened_count"] == 1  # ETH still opened
