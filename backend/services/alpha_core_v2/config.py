"""Alpha Core v2 — config. Own namespace (ALPHA_V2_*), never Legacy's env."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float, lo: float = None, hi: float = None) -> float:
    raw = (os.environ.get(name) or "").strip()
    try:
        v = float(raw) if raw else default
    except (TypeError, ValueError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _i(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA",
    "AMZN", "GOOGL", "META", "TSLA", "AVGO",
]


@dataclass
class Config:
    enabled: bool
    universe: list
    desired_notional: float      # what the strategy wants (ceiling)
    alloc_pct: float             # max fraction of equity per position
    cash_reserve: float          # cash kept unspent
    min_trade: float             # broker minimum order amount
    confidence_floor: float
    max_positions: int
    db_path: str

    @classmethod
    def load(cls) -> "Config":
        uni_raw = (os.environ.get("ALPHA_V2_UNIVERSE") or "").strip()
        universe = (
            [s.strip().upper() for s in uni_raw.split(",") if s.strip()]
            if uni_raw else list(DEFAULT_UNIVERSE)
        )
        return cls(
            enabled=(os.environ.get("ALPHA_CORE_V2") or "0").strip()
            in ("1", "true", "True", "yes", "on"),
            universe=universe,
            desired_notional=_f("ALPHA_V2_MAX_NOTIONAL_USD", 25.0, 1.0, 100000.0),
            alloc_pct=_f("ALPHA_V2_ALLOC_PCT", 0.20, 0.0, 1.0),
            cash_reserve=_f("ALPHA_V2_CASH_RESERVE_USD", 5.0, 0.0),
            min_trade=_f("ALPHA_V2_MIN_TRADE_USD", 1.0, 0.01),
            confidence_floor=_f("ALPHA_V2_CONFIDENCE_FLOOR", 0.55, 0.0, 1.0),
            max_positions=_i("ALPHA_V2_MAX_CONCURRENT_POSITIONS", 5),
            db_path=(os.environ.get("ALPHA_V2_DB") or "").strip()
            or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "data", "alpha_core_v2.sqlite"),
        )
