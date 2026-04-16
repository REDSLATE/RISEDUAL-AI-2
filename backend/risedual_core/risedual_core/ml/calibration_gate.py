"""Backward-compatibility shim — delegates to risedual_core.ml.calibration.

Existing scripts (backtest.py, etc.) import from this module.
All logic now lives in calibration.py; this shim wraps it.
"""

from __future__ import annotations

import logging
from typing import Any

from risedual_core.ml.calibration import (
    check_all_gates,
    check_tier1,
    check_tier2,
    check_tier3,
    kelly_fraction,
    half_kelly_position,
)

logger = logging.getLogger(__name__)


def is_alert_ready(stats: Any) -> bool:
    """Tier 1 readiness check (backward-compat wrapper)."""
    result = check_tier1(
        accuracy=stats.accuracy,
        n_predictions=stats.n_predictions,
        ece=stats.ece,
    )
    return result.unlocked


def is_paper_trade_ready(stats: Any, backtest: Any) -> bool:
    """Tier 2 readiness check (backward-compat wrapper)."""
    t1 = check_tier1(stats.accuracy, stats.n_predictions, stats.ece)
    result = check_tier2(
        accuracy=stats.accuracy,
        sharpe=backtest.sharpe_ratio,
        max_drawdown=backtest.max_drawdown,
        tier1_unlocked=t1.unlocked,
    )
    return result.unlocked


def is_live_ready(stats: Any, backtest: Any, days_live: int) -> bool:
    """Tier 3 readiness check (backward-compat wrapper)."""
    t1 = check_tier1(stats.accuracy, stats.n_predictions, stats.ece)
    t2 = check_tier2(
        accuracy=stats.accuracy,
        sharpe=backtest.sharpe_ratio,
        max_drawdown=backtest.max_drawdown,
        tier1_unlocked=t1.unlocked,
    )
    result = check_tier3(
        accuracy=stats.accuracy,
        sharpe=backtest.sharpe_ratio,
        max_drawdown=backtest.max_drawdown,
        live_days=days_live,
        user_opted_in=False,
        tier2_unlocked=t2.unlocked,
    )
    return result.unlocked


def gate_status(
    stats: Any | None,
    backtest: Any | None = None,
    days_live: int = 0,
) -> dict:
    """Full gate status for all three tiers (backward-compat wrapper)."""
    if stats is None:
        return {
            "tier1_alerts": False,
            "tier2_paper": False,
            "tier3_live": False,
            "reason": "No calibration stats available. Model not yet trained.",
        }

    sharpe = backtest.sharpe_ratio if backtest else 0.0
    max_drawdown = backtest.max_drawdown if backtest else 1.0

    gate = check_all_gates(
        accuracy=stats.accuracy,
        n_predictions=stats.n_predictions,
        ece=stats.ece,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        live_days=days_live,
        user_opted_in=False,
    )

    blockers: list[str] = []
    if not gate.tier1.unlocked:
        blockers.append(gate.tier1.reason)

    return {
        "tier1_alerts": gate.tier1.unlocked,
        "tier2_paper": gate.tier2.unlocked,
        "tier3_live": gate.tier3.unlocked,
        "stats": stats.model_dump() if hasattr(stats, "model_dump") else {},
        "backtest": backtest.model_dump() if backtest and hasattr(backtest, "model_dump") else None,
        "days_live": days_live,
        "blockers": blockers,
    }
