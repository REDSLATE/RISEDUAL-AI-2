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
    compute_greeks, compute_greeks_for_contract,
    MIN_T_YEARS,
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


# ── Greeks engine (Phase 2) ─────────────────────────────────────────
#
# Reference values cross-checked against Hull's spreadsheet (Options,
# Futures, and Other Derivatives, 10th ed.) for the canonical case
# S=K=100, T=1y, r=5%, σ=20%. Deltas use retail conventions (theta
# per-day, vega/rho per 1%).


def test_greeks_call_atm_hull_reference():
    """S=K=100, T=1, r=5%, σ=20% → delta ≈ 0.6368, gamma ≈ 0.0188,
    annual theta ≈ -6.41 → per-day ≈ -0.0176, vega per 1% ≈ 0.3752,
    rho per 1% ≈ 0.5323."""
    g = compute_greeks(100, 100, 1.0, 0.20, "call", risk_free=0.05)
    assert g["delta"] == pytest.approx(0.6368, abs=0.005)
    assert g["gamma"] == pytest.approx(0.0188, abs=0.002)
    assert g["theta"] == pytest.approx(-0.0176, abs=0.002)
    assert g["vega"] == pytest.approx(0.3752, abs=0.005)
    assert g["rho"] == pytest.approx(0.5323, abs=0.01)


def test_greeks_put_atm_hull_reference():
    """Same setup, put side.
    delta ≈ -0.3632, gamma identical, theta ≈ -0.0046,
    vega identical, rho ≈ -0.4189."""
    g = compute_greeks(100, 100, 1.0, 0.20, "put", risk_free=0.05)
    assert g["delta"] == pytest.approx(-0.3632, abs=0.005)
    assert g["gamma"] == pytest.approx(0.0188, abs=0.002)
    assert g["vega"] == pytest.approx(0.3752, abs=0.005)
    assert g["rho"] == pytest.approx(-0.4189, abs=0.01)


def test_greeks_delta_bounds():
    """Deep ITM call delta → 1.0; deep OTM call delta → 0.
    Deep ITM put delta → -1.0; deep OTM put delta → 0."""
    itm_call = compute_greeks(200, 100, 1.0, 0.20, "call")
    otm_call = compute_greeks(50, 100, 1.0, 0.20, "call")
    itm_put = compute_greeks(50, 100, 1.0, 0.20, "put")
    otm_put = compute_greeks(200, 100, 1.0, 0.20, "put")
    assert itm_call["delta"] > 0.95
    assert otm_call["delta"] < 0.1
    assert itm_put["delta"] < -0.95
    assert otm_put["delta"] > -0.1


def test_greeks_call_put_parity():
    """Delta parity: delta_call - delta_put = 1 (for non-dividend
    paying underlying). Holds to float precision."""
    c = compute_greeks(100, 95, 0.5, 0.25, "call", risk_free=0.03)
    p = compute_greeks(100, 95, 0.5, 0.25, "put", risk_free=0.03)
    assert (c["delta"] - p["delta"]) == pytest.approx(1.0, abs=0.005)
    # Gamma identical (same formula).
    assert c["gamma"] == pytest.approx(p["gamma"], abs=0.0001)
    # Vega identical.
    assert c["vega"] == pytest.approx(p["vega"], abs=0.0001)


def test_greeks_gamma_positive_always():
    """Gamma is always non-negative, for both calls and puts."""
    for otype in ("call", "put"):
        for spot in (50, 100, 150):
            g = compute_greeks(spot, 100, 1.0, 0.20, otype)
            assert g["gamma"] >= 0


def test_greeks_vega_atm_peak():
    """Vega peaks ATM. 100/100 strike should have higher vega than
    80/100 or 120/100 at the same σ/T."""
    atm = compute_greeks(100, 100, 0.5, 0.30, "call")["vega"]
    itm = compute_greeks(120, 100, 0.5, 0.30, "call")["vega"]
    otm = compute_greeks(80, 100, 0.5, 0.30, "call")["vega"]
    assert atm > itm
    assert atm > otm


def test_greeks_for_contract_ivpercent_conversion():
    """`iv_percent=30` should produce same Greeks as
    `compute_greeks(..., iv=0.30)` — the route helper converts
    percent-units to decimal."""
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=365)).date().isoformat()
    route = compute_greeks_for_contract(
        underlying_price=100, strike=100, expiry=future,
        option_type="call", iv_percent=30,
    )
    # Route output is a superset (adds mid_price + years_to_expiry).
    assert "delta" in route and "gamma" in route
    assert "mid_price" in route
    assert route["years_to_expiry"] == pytest.approx(1.0, abs=0.01)
    assert route["mid_price"] > 0


def test_greeks_theta_negative_for_atm_long_options():
    """Long ATM options always decay (theta ≤ 0). Note: deep ITM
    European puts can have positive theta when rates are high enough
    — that's a known BS quirk, not a bug. We only pin the ATM case."""
    for otype in ("call", "put"):
        g = compute_greeks(100, 100, 0.5, 0.25, otype)
        assert g["theta"] <= 0


def test_greeks_min_vol_floor_does_not_crash():
    """σ=0 input must not divide-by-zero inside the Greek formulae."""
    g = compute_greeks(100, 100, 1.0, 0.0, "call")
    assert "delta" in g and "gamma" in g
    # Gamma at σ→0 is huge by convention — we just want no crash.
    assert g["gamma"] >= 0
