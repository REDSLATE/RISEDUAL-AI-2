"""Lane-separated executor — crypto lane.

24/7 lane: NO market-hours gate. Crypto venues are continuous; an
equity-style "is the session open?" check would produce false skips
during normal weekend trading.

Symbolic gates (deterministic, run BEFORE Fast Veto / Council):
  1. (Future) Per-pair maintenance / halted-pair lookup
  2. (Future) Per-jurisdiction blocked-symbol lookup

For now there is no blocking gate — the lane runs through to the
shared executor body with ``lane="crypto"`` so the Fast Veto layer
applies crypto-tuned thresholds and the shadow log is tagged.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.executors._shared import CRYPTO_LANE

logger = logging.getLogger(__name__)


async def execute_crypto_signal(
    signal: dict,
    market_data: dict,
    tier3_readiness: dict,
    config: Any,
    open_positions: Optional[List[dict]] = None,
    equity_curve: Optional[List[float]] = None,
    bot_capital: Optional[float] = None,
) -> Dict[str, Any]:
    """Crypto-lane executor entry point.

    Same signature as the legacy ``execute_signal`` so callers can
    swap directly. The lane runs 24/7 — no market-hours pre-gate.
    """
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
        lane=CRYPTO_LANE.name,
    )
