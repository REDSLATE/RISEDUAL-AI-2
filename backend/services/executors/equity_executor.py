"""Lane-separated executor — equity lane.

24/5 lane: gated on the US regular session being open. After hours,
returns a ``MARKET_CLOSED`` skip without hitting the broker — quotes
during off-hours are not executable and routing them masks real
intent (e.g. a stale signal from a yesterday's-close scan).

Symbolic gates (deterministic, run BEFORE Fast Veto / Council):
  1. Market hours — Mon–Fri 13:30–21:00 UTC
  2. (Future) Hard regulatory blocks (PDT, halted symbol)

Once the gates pass, delegates to the shared executor body in
``trading_bot_service.execute_signal`` with ``lane="equity"`` so the
Fast Veto layer applies equity-tuned thresholds and the shadow log
is tagged.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.executors._shared import EQUITY_LANE, is_equity_market_open

logger = logging.getLogger(__name__)


async def execute_equity_signal(
    signal: dict,
    market_data: dict,
    tier3_readiness: dict,
    config: Any,
    open_positions: Optional[List[dict]] = None,
    equity_curve: Optional[List[float]] = None,
    bot_capital: Optional[float] = None,
) -> Dict[str, Any]:
    """Equity-lane executor entry point.

    Same signature as the legacy ``execute_signal`` so callers can
    swap directly. The only behavioural difference is the
    market-hours pre-gate that runs before any kill-switch / fast-
    veto / sizing work.
    """
    if not is_equity_market_open():
        logger.info(
            "[equity-lane] %s skipped: market closed",
            signal.get("symbol"),
        )
        return {
            "skipped": True,
            "reason": "MARKET_CLOSED",
            "lane": EQUITY_LANE.name,
        }

    # Lazy import to avoid a circular import at module load
    # (trading_bot_service imports executors.__init__).
    from services.trading_bot_service import execute_signal as _core_execute
    return await _core_execute(
        signal=signal,
        market_data=market_data,
        tier3_readiness=tier3_readiness,
        config=config,
        open_positions=open_positions,
        equity_curve=equity_curve,
        bot_capital=bot_capital,
        lane=EQUITY_LANE.name,
    )
