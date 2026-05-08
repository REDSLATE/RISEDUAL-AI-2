"""Lane-separated executor package.

Public entry points:

* :func:`execute_equity_signal` — US-equity lane, market-hours gated
* :func:`execute_crypto_signal` — crypto lane, 24/7

Both have identical signatures to the legacy
``services.trading_bot_service.execute_signal`` and dispatch into
the same shared body with a ``lane=`` tag. The shared body uses the
tag to (a) pick the correct Fast Veto thresholds and (b) tag the
shadow-delta log so analytics can disaggregate equity vs crypto
performance.

Lane detection helper :func:`detect_lane` is exported for the thin
router that lives inside ``trading_bot_service.execute_signal``.
"""
from __future__ import annotations

from services.executors._shared import (
    CRYPTO_LANE,
    EQUITY_LANE,
    LaneConfig,
    detect_lane,
    is_equity_market_open,
    lane_config_for,
)
from services.executors.crypto_executor import execute_crypto_signal
from services.executors.equity_executor import execute_equity_signal

__all__ = [
    "CRYPTO_LANE",
    "EQUITY_LANE",
    "LaneConfig",
    "detect_lane",
    "execute_crypto_signal",
    "execute_equity_signal",
    "is_equity_market_open",
    "lane_config_for",
]
