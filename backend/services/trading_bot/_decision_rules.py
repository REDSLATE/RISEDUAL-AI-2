"""Pure decision rules for the trading-bot service.

Step 4D extraction (2026-05-09). Verbatim moves of the four
portfolio/exposure eligibility functions from
``services/trading_bot_service.py``. Pure functions only — no DB,
no broker, no mutation, no kill-switch state changes, no env reads,
no logging. Inputs in, scalar/None out.

Functions moved:
  * ``get_total_exposure``         — sum of size_usd
  * ``get_open_trade_count``       — len(open_positions)
  * ``get_sector_exposure``        — sector-share ratio
  * ``apply_portfolio_constraints``— eligibility/headroom check returning
                                     a non-negative trade size (0 = block)

Constants (``MAX_CONCURRENT_TRADES`` / ``MAX_PORTFOLIO_EXPOSURE`` /
``MAX_SECTOR_EXPOSURE_PCT``) are imported from ``._constants``. No
existing test monkeypatches the ``tbs.MAX_*`` attributes; reading
directly from ``_constants`` is byte-equivalent for all current
callers and tests.

The shim in ``trading_bot_service`` re-exports these so external
attribute access (`tbs.apply_portfolio_constraints`, etc., used
extensively in ``test_portfolio_risk_engine.py``) still works.
"""
from services.trading_bot._constants import (
    MAX_CONCURRENT_TRADES,
    MAX_PORTFOLIO_EXPOSURE,
    MAX_SECTOR_EXPOSURE_PCT,
)


def get_total_exposure(open_positions: list[dict]) -> float:
    """Sum USD-notional size across a list of open positions.

    Accepts any iterable of dicts with a `size_usd` key. Missing or
    non-numeric values count as 0 — never raises.
    """
    total = 0.0
    for p in open_positions or []:
        try:
            total += float(p.get("size_usd", 0) or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def get_open_trade_count(open_positions: list[dict]) -> int:
    """Number of open positions — used for the concurrency cap."""
    return len(open_positions or [])


def get_sector_exposure(sector: str, open_positions: list[dict]) -> float:
    """Return the given sector's share of total exposure (0.0 - 1.0).

    Case-insensitive sector match on a position's ``sector`` key.
    Returns 0.0 when there are no open positions, total exposure is
    zero, or `sector` is falsy.
    """
    if not sector or not open_positions:
        return 0.0
    total = get_total_exposure(open_positions)
    if total <= 0:
        return 0.0
    target = sector.lower()
    sector_total = 0.0
    for p in open_positions:
        if (p.get("sector") or "").lower() != target:
            continue
        try:
            sector_total += float(p.get("size_usd", 0) or 0)
        except (TypeError, ValueError):
            continue
    return round(sector_total / total, 4)


def apply_portfolio_constraints(
    new_trade_size: float,
    open_positions: list[dict],
    signal_sector: str | None = None,
) -> float:
    """Shrink the proposed trade size to fit remaining portfolio
    headroom, or zero it when any cap is saturated.

    Rules (evaluated in order):
      1. If `open_trade_count >= MAX_CONCURRENT_TRADES` → return 0.
      2. `remaining = MAX_PORTFOLIO_EXPOSURE - total_exposure`. If
         `remaining <= 0` → return 0.
      3. If `signal_sector` is provided and that sector already
         represents more than `MAX_SECTOR_EXPOSURE_PCT` of total
         exposure → return 0 (no stacking in an over-concentrated
         sector).
      4. Otherwise return `min(new_trade_size, remaining)`.

    Zero is the signal to callers to skip the trade with a
    `"portfolio limits reached"` reason.
    """
    if get_open_trade_count(open_positions) >= MAX_CONCURRENT_TRADES:
        return 0.0
    remaining = MAX_PORTFOLIO_EXPOSURE - get_total_exposure(open_positions)
    if remaining <= 0:
        return 0.0
    if (
        signal_sector
        and get_sector_exposure(signal_sector, open_positions) > MAX_SECTOR_EXPOSURE_PCT
    ):
        return 0.0
    return round(min(float(new_trade_size), remaining), 2)
