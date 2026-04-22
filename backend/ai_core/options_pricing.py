"""Black-Scholes option pricer.

Used by the paper-trading service to simulate option fills when OPRA
live quotes aren't available (which is always, on our free-tier
market data plan). Pure functions — no I/O, no state — so unit tests
can pin numerical output without mocks.

Notation (standard BS):
  S  — underlying spot price
  K  — strike
  T  — time to expiry, in years
  r  — risk-free rate (default 0.05 ≈ 3-month T-bill yield; good
       enough for paper-trade fills. Live trading would pull from
       FRED.)
  σ  — implied volatility (annualized, as decimal: 0.30 = 30%)

Outputs are rounded to 4 decimals — tighter than penny precision on
the cheapest contracts, loose enough to avoid float-drift surprises
in snapshot tests.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

# Risk-free rate default. Low-fidelity on purpose — paper trading
# doesn't need a term-structure-aware rate curve. Callers can
# override per-call if they want.
DEFAULT_RISK_FREE_RATE = 0.05

# Implied vol default when the scanner row doesn't carry a value.
# 0.30 (30%) is a reasonable mid-point for S&P 500 sector ETFs;
# individual names can run far higher (BYND is quoting 503% IV in
# the user's screenshot) but we'd rather under-price than mis-match.
DEFAULT_IV = 0.30

# Floors. Black-Scholes is continuous but mathematically undefined at
# T=0 (exercise value only) and σ=0 (deterministic payoff). We clamp
# to a tiny epsilon so same-day expiries still produce a sane fill
# price at the intrinsic value.
MIN_T_YEARS = 1.0 / 365.0 / 24.0   # 1 hour
MIN_VOL = 0.01                      # 1% floor


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via `math.erf`. Accurate to machine
    precision, no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def years_to_expiry(expiry: str | date | datetime, now: datetime | None = None) -> float:
    """Convert an expiry marker to a year-fraction.

    Accepts ISO-date strings (`"2026-06-18"`) or `date`/`datetime`.
    `now` defaults to UTC now; passing it explicitly makes tests
    deterministic.

    Returns a positive float even if the expiry is in the past — the
    BS formula collapses to intrinsic value at T→0, which is the
    correct paper-fill behaviour for an expired contract.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if isinstance(expiry, str):
        # ISO-8601 with or without time component.
        expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    if isinstance(expiry, datetime):
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        delta = expiry - now
    else:  # date
        target = datetime(expiry.year, expiry.month, expiry.day, 16, 0, tzinfo=timezone.utc)
        delta = target - now
    years = delta.total_seconds() / (365.0 * 24.0 * 3600.0)
    return max(years, MIN_T_YEARS)


def bs_price(
    spot: float,
    strike: float,
    years: float,
    iv: float,
    option_type: str,
    risk_free: float = DEFAULT_RISK_FREE_RATE,
) -> float:
    """Black-Scholes European option price.

    `option_type` is "call" or "put" (case-insensitive). Any other
    string falls back to "call" — options scanners almost never
    deliver malformed types, but we don't want a typo to crash a
    paper trade.
    """
    t = max(float(years), MIN_T_YEARS)
    sigma = max(float(iv), MIN_VOL)
    s = float(spot)
    k = float(strike)

    d1 = (math.log(s / k) + (risk_free + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)

    if option_type.lower() == "put":
        price = k * math.exp(-risk_free * t) * _norm_cdf(-d2) - s * _norm_cdf(-d1)
    else:
        price = s * _norm_cdf(d1) - k * math.exp(-risk_free * t) * _norm_cdf(d2)

    # Clamp to ≥ $0.01 — exchanges don't list sub-penny contracts
    # and deep-OTM BS output can drift negative by a few bps from
    # float noise.
    return max(round(price, 4), 0.01)


def estimate_fill_price(
    underlying_price: float,
    strike: float,
    expiry: str | date | datetime,
    option_type: str,
    iv_percent: float | None = None,
    side: str = "buy",
) -> float:
    """Top-level helper the paper-trade service calls.

    `iv_percent` is what the scanner rows display (e.g. `18.92` means
    18.92%). Pass `None` to use the `DEFAULT_IV`. Converts to the
    decimal BS expects internally.

    `side` — "buy" pays the ask, "sell" hits the bid. We simulate a
    1.5% bid-ask spread around the BS mid, so buys fill +0.75% and
    sells fill -0.75%. Real OPRA spreads vary wildly (pennies for
    SPY, dollars for illiquids); a flat percent is fine for paper.
    """
    iv = (iv_percent / 100.0) if iv_percent is not None else DEFAULT_IV
    years = years_to_expiry(expiry)
    mid = bs_price(underlying_price, strike, years, iv, option_type)

    spread_factor = 1.0075 if side.lower() == "buy" else 0.9925
    return max(round(mid * spread_factor, 4), 0.01)
