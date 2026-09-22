"""Alpha Core v2 — account-percentage sizing with first-class provenance.

The intended portfolio rule (restored 2026-06):

    available buying power → allocate ``alloc_pct`` (e.g. 3%) per NEW trade
    → trade if affordable and it clears the broker minimum.

The allocation base is the broker-authoritative **available/spendable buying
power**, NOT equity and NOT a fixed dollar target. Owning positions does not,
by itself, stop a trade — the account's remaining capital is the constraint.

The size is the SMALLEST of:
  * ``allocation_notional``  = spendable_balance * alloc_pct
  * ``risk_cap_notional``    = the absolute per-trade ceiling (existing cap)
  * ``affordable_ceiling``   = buying_power - cash_reserve

Then floored to fractional shares (never spend more than affordable), bumped
only if flooring dips under the broker minimum order. Every step is recorded.
"""
from __future__ import annotations

import math

from services.alpha_core_v2.contracts import SizePlan


def plan_size(
    *,
    buying_power: float,
    alloc_pct: float,
    per_trade_cap: float,
    cash_reserve: float,
    mark: float,
    min_trade: float,
) -> SizePlan:
    bp = max(0.0, float(buying_power))
    # Allocation base = currently available/spendable buying power.
    spendable_balance = bp
    allocation_pct = max(0.0, float(alloc_pct))
    allocation_notional = spendable_balance * allocation_pct
    # Absolute per-trade risk cap (existing safety ceiling), preserved.
    risk_cap_notional = max(0.0, float(per_trade_cap))
    target = min(allocation_notional, risk_cap_notional)
    # Affordability/reserve: never spend into the reserve.
    affordable_ceiling = max(0.0, bp - float(cash_reserve))
    final = min(target, affordable_ceiling)

    # Which constraint bound the size below the 3% allocation (for the receipt)?
    resized = final < allocation_notional - 1e-9
    resize_reason = None
    if resized:
        resize_reason = "risk_cap" if risk_cap_notional <= affordable_ceiling + 1e-9 \
            else "buying_power"

    def _build(qty: float, final_notional: float) -> SizePlan:
        return SizePlan(
            spendable_balance=round(spendable_balance, 4),
            allocation_pct=round(allocation_pct, 6),
            allocation_notional=round(allocation_notional, 4),
            risk_cap_notional=round(risk_cap_notional, 4),
            final_notional=round(final_notional, 4),
            execution_price=round(float(mark), 6),
            quantity=qty,
            remaining_buying_power=round(bp - final_notional, 4),
            resize_reason=resize_reason,
            resized=resized,
        )

    if mark <= 0 or final < min_trade:
        # Not enough spendable capital for a valid order — quantity 0; caller BLOCKS.
        return _build(0.0, 0.0)

    # Fractional shares: floor to 4 dp so we never spend MORE than affordable;
    # bump only if flooring dips under the broker minimum order.
    qty = math.floor((final / mark) * 10000.0) / 10000.0
    if qty * mark < min_trade:
        qty = math.ceil((min_trade / mark) * 10000.0) / 10000.0

    return _build(qty, qty * mark)
