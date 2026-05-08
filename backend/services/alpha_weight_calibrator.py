"""Alpha weight calibrator — sizes positions from min(equity, cash).

Hard rule: NEVER size from buying_power. Margin overstates capacity;
the user has explicitly forbidden any sizing path that uses it.

Public API:
  * :func:`calibrate_position_size` — returns USD notional for one trade

Behaviour:
  * Caller passes in :class:`AccountState` (cash + equity + open exposure).
  * We compute ``available = min(cash, equity_value) - open_exposure``.
  * Floor at 0.
  * Cap by ``ALPHA_MAX_TOTAL_EXPOSURE_USD`` (env, default $500).
  * Split by ``ALPHA_SLOTS`` (env, default 5) → per-trade notional.
  * If ``available`` after cap is below ``ALPHA_MIN_TICKET_USD`` (env,
    default $1) -> returns 0.0 (no trade).

This sizing is the floor for the executor; RoadGuard may further
reduce in the second pass.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("[alpha_calibrator] bad value %s=%r; using %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("[alpha_calibrator] bad value %s=%r; using %s", name, raw, default)
        return default


@dataclass
class AccountState:
    cash_usd: float
    equity_value_usd: float
    open_exposure_usd: float = 0.0

    def __post_init__(self):
        if self.cash_usd < 0 or self.equity_value_usd < 0:
            raise ValueError("AccountState requires non-negative cash and equity")


@dataclass
class SizingDecision:
    notional_usd: float
    capped_by: Optional[str]   # "ABSOLUTE_CAP" | "AVAILABLE" | "MIN_TICKET" | None
    available_usd: float
    slots: int
    diagnostics: dict


def calibrate_position_size(state: AccountState) -> SizingDecision:
    max_total = _env_float("ALPHA_MAX_TOTAL_EXPOSURE_USD", 500.0)
    slots = max(1, _env_int("ALPHA_SLOTS", 5))
    min_ticket = _env_float("ALPHA_MIN_TICKET_USD", 1.0)

    base = min(state.cash_usd, state.equity_value_usd) - state.open_exposure_usd
    available = max(0.0, base)

    capped_by: Optional[str] = None
    capped = available
    if capped > max_total:
        capped = max_total
        capped_by = "ABSOLUTE_CAP"

    per_trade = capped / slots if slots > 0 else 0.0

    if per_trade < min_ticket:
        return SizingDecision(
            notional_usd=0.0,
            capped_by="MIN_TICKET",
            available_usd=available,
            slots=slots,
            diagnostics={
                "min_required": min_ticket,
                "would_size": per_trade,
                "max_total": max_total,
                "open_exposure": state.open_exposure_usd,
                "cash": state.cash_usd,
                "equity": state.equity_value_usd,
            },
        )

    return SizingDecision(
        notional_usd=per_trade,
        capped_by=capped_by,
        available_usd=available,
        slots=slots,
        diagnostics={
            "max_total": max_total,
            "open_exposure": state.open_exposure_usd,
            "cash": state.cash_usd,
            "equity": state.equity_value_usd,
        },
    )
