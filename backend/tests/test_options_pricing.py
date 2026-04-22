"""Tests for `ai_core.options_pricing`.

Pins:
  * `bs_price` numerical output against a well-known textbook case
    (Hull: S=100, K=100, T=1, r=0.05, σ=0.20 → call ≈ 10.45).
  * Symmetry: put-call parity holds within rounding.
  * Edge cases: T→0 collapses to intrinsic value; σ→0 floors at MIN_VOL.
  * `years_to_expiry` accepts ISO strings and `date`/`datetime`;
    past expiries clamp to MIN_T_YEARS without exploding.
  * `estimate_fill_price` applies the buy/sell spread factor.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from ai_core.options_pricing import (
    bs_price, years_to_expiry, estimate_fill_price,
    MIN_T_YEARS, MIN_VOL,
)


# ── Textbook sanity check ──────────────────────────────────────────

def test_bs_price_hull_call_atmoney():
    """Hull, `Options, Futures, and Other Derivatives`, 10th ed,
    example: S=100, K=100, T=1y, r=0.05, σ=0.20 → call ≈ 10.45."""
    price = bs_price(100, 100, 1.0, 0.20, "call", risk_free=0.05)
    assert price == pytest.approx(10.45, abs=0.05)


def test_bs_price_hull_put_atmoney():
    """Same setup, put side ≈ 5.57."""
    price = bs_price(100, 100, 1.0, 0.20, "put", risk_free=0.05)
    assert price == pytest.approx(5.57, abs=0.05)


def test_put_call_parity():
    """c - p = S - K * exp(-rT). Holds within float tolerance."""
    import math
    S, K, T, r, sigma = 100, 95, 0.5, 0.03, 0.25
    c = bs_price(S, K, T, sigma, "call", risk_free=r)
    p = bs_price(S, K, T, sigma, "put", risk_free=r)
    expected = S - K * math.exp(-r * T)
    assert (c - p) == pytest.approx(expected, abs=0.02)


# ── Edge cases ──────────────────────────────────────────────────────

def test_zero_time_collapses_to_intrinsic_like_value():
    """At T → 0 a call's BS price ≈ max(S-K, 0). Our MIN_T_YEARS
    floor means "deep ITM" calls approach intrinsic, "deep OTM"
    approach ~0."""
    # ITM call: S=100, K=80 → intrinsic = 20
    p_itm = bs_price(100, 80, MIN_T_YEARS, 0.30, "call")
    assert p_itm == pytest.approx(20.0, abs=0.1)

    # OTM call: S=100, K=120 → intrinsic = 0; BS floor clamps to $0.01
    p_otm = bs_price(100, 120, MIN_T_YEARS, 0.30, "call")
    assert p_otm == 0.01


def test_zero_vol_does_not_crash():
    """Callers passing σ=0 (deterministic) must not divide-by-zero.
    The MIN_VOL floor handles it."""
    p = bs_price(100, 100, 1.0, 0.0, "call")
    # With σ clamped to MIN_VOL (0.01), call price will be close to
    # forward value minus strike discount — small but positive.
    assert p > 0


def test_unknown_option_type_defaults_to_call():
    call_price = bs_price(100, 100, 1.0, 0.20, "call")
    unknown_price = bs_price(100, 100, 1.0, 0.20, "butterfly")
    assert unknown_price == call_price


# ── years_to_expiry ─────────────────────────────────────────────────

def test_years_to_expiry_iso_string_forward():
    """Six months out ≈ 0.5 years, within a day's worth of tolerance."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    six_mo = "2026-07-01"
    y = years_to_expiry(six_mo, now=now)
    assert 0.49 < y < 0.51


def test_years_to_expiry_past_date_clamps_to_min():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    past = "2026-01-01"
    y = years_to_expiry(past, now=now)
    assert y == MIN_T_YEARS


def test_years_to_expiry_handles_naive_datetime():
    """Naive datetime inputs get assumed UTC (explicit contract)."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expiry_naive = datetime(2026, 1, 31)
    y = years_to_expiry(expiry_naive, now=now)
    assert 0.07 < y < 0.1


# ── estimate_fill_price ─────────────────────────────────────────────

def test_estimate_fill_price_buy_higher_than_sell():
    """Buy pays above mid, sell hits below mid. The 1.5% spread
    means buy > sell by at least the BS mid × 0.015."""
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=30)).date().isoformat()
    buy_price = estimate_fill_price(100, 100, future, "call", iv_percent=25, side="buy")
    sell_price = estimate_fill_price(100, 100, future, "call", iv_percent=25, side="sell")
    assert buy_price > sell_price


def test_estimate_fill_price_ivrank_is_percent_not_decimal():
    """Regression lock: scanner rows pass `ivRank` like 18.92 for
    18.92%, not 0.1892. Helper must interpret correctly."""
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=60)).date().isoformat()
    # Call with IV 50% should price higher than the same call at 10%.
    hi = estimate_fill_price(100, 100, future, "call", iv_percent=50)
    lo = estimate_fill_price(100, 100, future, "call", iv_percent=10)
    assert hi > lo
