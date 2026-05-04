"""Tests for ``services.slippage_simulator``.

Pins these invariants:

1. ``apply_entry_slippage`` is pure — same input → same output.
2. LONG entry fills at ASK; SHORT entry fills at BID.
3. Missing bid/ask → degrade to mid_only with ``slippage_bps=0``.
4. Empty / None / unknown-direction quote → ``unknown`` method,
   ``fill_price=0`` so caller skips.
5. ``slippage_bps`` is positive for adverse fills (always
   "how much worse than mid").
6. ``apply_exit_slippage`` mirrors entry but flips sides:
   close-LONG sells at bid, close-SHORT buys at ask.
"""
from __future__ import annotations

import pytest

from services.slippage_simulator import (
    SlippageResult,
    apply_entry_slippage,
    apply_exit_slippage,
)


# ── Entry side ────────────────────────────────────────────────────


def test_long_entry_fills_at_ask():
    quote = {"price": 100.0, "bid": 99.5, "ask": 100.5, "last": 100.0}
    r = apply_entry_slippage(quote, "LONG")
    assert r.fill_price == 100.5
    assert r.method == "ask_fill"
    # spread = (100.5-99.5)/100 * 10000 = 100 bps; slippage from
    # mid is half of that = 50 bps
    assert r.slippage_bps == pytest.approx(50.0, abs=0.5)


def test_short_entry_fills_at_bid():
    quote = {"price": 100.0, "bid": 99.5, "ask": 100.5, "last": 100.0}
    r = apply_entry_slippage(quote, "SHORT")
    assert r.fill_price == 99.5
    assert r.method == "bid_fill"
    # SHORT seller hit on bid → bid < mid is adverse → positive bps.
    assert r.slippage_bps == pytest.approx(50.0, abs=0.5)


@pytest.mark.parametrize("d", ["LONG", "BUY", "UP", "BULLISH", "STRONG_BUY"])
def test_long_aliases_all_resolve(d):
    quote = {"price": 100.0, "bid": 99.5, "ask": 100.5}
    assert apply_entry_slippage(quote, d).method == "ask_fill"


@pytest.mark.parametrize("d", ["SHORT", "SELL", "DOWN", "BEARISH", "WEAK_SELL"])
def test_short_aliases_all_resolve(d):
    quote = {"price": 100.0, "bid": 99.5, "ask": 100.5}
    assert apply_entry_slippage(quote, d).method == "bid_fill"


def test_unknown_direction_falls_through_to_mid_only():
    quote = {"price": 100.0, "bid": 99.5, "ask": 100.5}
    r = apply_entry_slippage(quote, "HOLD")
    assert r.method == "mid_only"
    assert r.slippage_bps == 0.0


def test_missing_bid_ask_falls_back_to_mid_only():
    """Legacy provider — only ``price`` field. No slippage applied."""
    quote = {"price": 100.0}
    r = apply_entry_slippage(quote, "LONG")
    assert r.method == "mid_only"
    assert r.fill_price == 100.0
    assert r.slippage_bps == 0.0


def test_only_last_trade_no_quote():
    """Provider returned only ``last`` (after-hours one-sided)."""
    quote = {"last": 100.0, "bid": None, "ask": None}
    r = apply_entry_slippage(quote, "LONG")
    assert r.method == "mid_only"
    assert r.fill_price == 100.0


def test_empty_quote_returns_unknown():
    r = apply_entry_slippage({}, "LONG")
    assert r.method == "unknown"
    assert r.fill_price == 0.0


def test_none_quote_returns_unknown():
    r = apply_entry_slippage(None, "LONG")
    assert r.method == "unknown"


def test_zero_prices_treated_as_missing():
    """Bid/ask of 0 (Alpaca after-hours one-sided) falls back to mid."""
    quote = {"price": 100.0, "bid": 99.5, "ask": 0, "last": 100.0}
    r = apply_entry_slippage(quote, "LONG")
    assert r.method == "mid_only"
    assert r.fill_price == 100.0


def test_purity_no_input_mutation():
    quote = {"price": 100.0, "bid": 99.5, "ask": 100.5}
    snapshot = dict(quote)
    apply_entry_slippage(quote, "LONG")
    assert quote == snapshot


def test_returns_dataclass_with_all_fields():
    r = apply_entry_slippage(
        {"price": 100.0, "bid": 99.5, "ask": 100.5}, "LONG",
    )
    assert isinstance(r, SlippageResult)
    assert r.bid == 99.5
    assert r.ask == 100.5
    assert r.mid == 100.0


# ── Exit side ─────────────────────────────────────────────────────


def test_close_long_sells_at_bid():
    quote = {"price": 100.0, "bid": 99.5, "ask": 100.5}
    r = apply_exit_slippage(quote, "LONG")
    assert r.fill_price == 99.5
    assert r.method == "bid_fill"
    # Closing long at bid → bid < mid adverse → +50 bps.
    assert r.slippage_bps == pytest.approx(50.0, abs=0.5)


def test_close_short_buys_at_ask():
    quote = {"price": 100.0, "bid": 99.5, "ask": 100.5}
    r = apply_exit_slippage(quote, "SHORT")
    assert r.fill_price == 100.5
    assert r.method == "ask_fill"
    assert r.slippage_bps == pytest.approx(50.0, abs=0.5)


def test_exit_missing_bid_ask_falls_back_to_mid():
    quote = {"price": 100.0}
    r = apply_exit_slippage(quote, "LONG")
    assert r.method == "mid_only"
    assert r.fill_price == 100.0


def test_exit_empty_quote_unknown():
    assert apply_exit_slippage(None, "LONG").method == "unknown"
    assert apply_exit_slippage({}, "LONG").method == "unknown"


# ── Integration: realistic Alpaca payload ────────────────────────


def test_realistic_alpaca_after_hours_qqq_payload():
    """Real payload from QQQ post-close — ask=671.98, bid=671.88."""
    quote = {
        "price": 671.93, "bid": 671.88, "ask": 671.98,
        "last": 672.25, "spread_bps": 1.49, "source": "alpaca",
    }
    long_r = apply_entry_slippage(quote, "LONG")
    short_r = apply_entry_slippage(quote, "SHORT")
    assert long_r.fill_price == 671.98
    assert short_r.fill_price == 671.88
    # Both should be ~half the spread = ~0.74 bps
    assert 0.5 < long_r.slippage_bps < 1.0
    assert 0.5 < short_r.slippage_bps < 1.0


def test_realistic_kraken_btc_payload():
    """Real payload from Kraken BTC — tight 0.01 bps spread."""
    quote = {
        "price": 80312.15, "bid": 80312.1, "ask": 80312.2,
        "last": 80312.1, "spread_bps": 0.01, "source": "kraken",
    }
    r = apply_entry_slippage(quote, "LONG")
    assert r.fill_price == 80312.2
    # Tight spread → tiny slippage.
    assert 0 < r.slippage_bps < 0.5
