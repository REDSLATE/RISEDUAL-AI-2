"""Alpha Core v2 — sizing with first-class provenance.

``desired`` is what the strategy wants (up to the configured ceiling). Risk
policy caps it to a fraction of EQUITY. Then it must fit inside affordable
buying power (minus a reserve). The order is the SMALLEST of those. Every
step is recorded so a resize is always explicit, never a silent partial.
"""
from __future__ import annotations

import math

from services.alpha_core_v2.contracts import SizePlan


def plan_size(
    *,
    desired_notional: float,
    equity: float,
    buying_power: float,
    alloc_pct: float,
    cash_reserve: float,
    mark: float,
    min_trade: float,
) -> SizePlan:
    desired = max(0.0, float(desired_notional))
    # Risk policy: never allocate more than alloc_pct of account EQUITY.
    account_cap = equity * alloc_pct if equity > 0 else desired
    risk_capped = min(desired, account_cap)
    # Affordability: what buying power (minus reserve) can actually cover.
    spendable_bp = max(0.0, buying_power - cash_reserve)
    affordable = min(risk_capped, spendable_bp)
    final = affordable

    # Which constraint bound the size (for the receipt)?
    resize_reason = None
    if final < desired - 1e-9:
        if spendable_bp <= risk_capped + 1e-9 and spendable_bp < desired:
            resize_reason = "buying_power"
        else:
            resize_reason = "risk_cap"

    if final < min_trade or mark <= 0:
        # Not enough to place a valid order — quantity 0; caller BLOCKS.
        return SizePlan(
            desired_notional=round(desired, 4),
            risk_capped_notional=round(risk_capped, 4),
            buying_power=round(buying_power, 4),
            buying_power_reserve=round(cash_reserve, 4),
            affordable_notional=round(affordable, 4),
            final_notional=round(final, 4),
            quantity=0.0,
            resize_reason=resize_reason,
            resized=final < desired - 1e-9,
        )

    # Fractional shares: floor to 4 dp so we never spend MORE than
    # affordable; bump only if flooring dips under the broker minimum.
    qty = math.floor((final / mark) * 10000.0) / 10000.0
    if qty * mark < min_trade:
        qty = math.ceil((min_trade / mark) * 10000.0) / 10000.0

    return SizePlan(
        desired_notional=round(desired, 4),
        risk_capped_notional=round(risk_capped, 4),
        buying_power=round(buying_power, 4),
        buying_power_reserve=round(cash_reserve, 4),
        affordable_notional=round(affordable, 4),
        final_notional=round(qty * mark, 4),
        quantity=qty,
        resize_reason=resize_reason,
        resized=final < desired - 1e-9,
    )
