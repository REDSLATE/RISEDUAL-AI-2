"""Lane-separated executor — shared configuration.

The neuro-symbolic split: each lane (equity / crypto) declares its
own deterministic constraints (the "logic engine" half) here. The
ML/Council "perception" half is the same for both — they only
diverge on the symbolic gates (market hours, spread tolerance,
liquidity floor) that should never be the same across asset classes.

Why two lanes:

* **Equity is gated by market hours.** Trading equities during a
  closed session is meaningless (no fills) and a real risk
  (after-hours quotes ≠ executable price). Equity executor MUST
  return ``MARKET_CLOSED`` skip during off-hours.
* **Crypto is 24/7.** Even an equity-style market-hours check would
  produce false skips on a Sunday at 3am.
* **Spread / liquidity thresholds differ by 5–10×.** A 75-bps spread
  is loose for AAPL, normal for a mid-cap altcoin. Sharing one
  threshold across both means either equity gets bypassed when it
  shouldn't, or crypto gets vetoed when it shouldn't.

This module owns the threshold tables. Callers use the dataclasses,
they don't hard-code numbers in the lane modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class LaneConfig:
    """Per-lane deterministic constraints for the executor.

    Frozen so a runtime caller can't accidentally mutate a shared
    threshold table (would break the symbolic-logic invariant).
    """
    name: str  # "equity" | "crypto"
    market_hours_required: bool

    # Fast Veto threshold overrides (None → use the layer's defaults)
    fast_veto_thresholds: Dict[str, float] = field(default_factory=dict)


# ── Equity lane ──
# Tighter spread / liquidity gates than the layer defaults because
# any equity quote that wide is either after-hours or a venue issue.
EQUITY_LANE: LaneConfig = LaneConfig(
    name="equity",
    market_hours_required=True,
    fast_veto_thresholds={
        "spread_bps_max": 50.0,         # 50 bps is wide for liquid US equity
        "liquidity_score_min": 0.30,    # tighter than the 0.20 default
        "drawdown_pct_max": 10.0,       # default
        "vol_score_max": 0.90,          # default
        "vol_low_conf_floor": 0.70,     # default
    },
)


# ── Crypto lane ──
# Crypto books are thinner, spreads wider, available 24/7. Looser
# thresholds prevent false positives that would torch crypto signal
# flow during normal book conditions.
CRYPTO_LANE: LaneConfig = LaneConfig(
    name="crypto",
    market_hours_required=False,
    fast_veto_thresholds={
        "spread_bps_max": 150.0,        # crypto spreads run 50–200 bps normally
        "liquidity_score_min": 0.10,    # mid-cap pairs sit in 0.15–0.25 range
        "drawdown_pct_max": 15.0,       # crypto realised vol is higher; allow deeper DD before veto
        "vol_score_max": 0.95,          # higher tolerance
        "vol_low_conf_floor": 0.60,     # slightly looser
    },
)


# ── Lane resolution ──

# Symbol heuristics for crypto pair detection — only used when the
# signal payload itself doesn't carry an explicit ``asset_type``.
# Conservative on purpose: false negatives (crypto routed to equity
# router) are caught by the next-layer market-hours gate; false
# positives (equity routed to crypto) would silently bypass the
# market-hours gate. Better to err toward equity.
_CRYPTO_SYMBOL_MARKERS = (
    "-USD", "/USD", "USDT", "USDC",
    "-EUR", "/EUR",
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "MATIC",
)


def detect_lane(signal: Dict[str, Any]) -> str:
    """Return ``"equity"`` or ``"crypto"`` for a signal payload.

    Resolution order:
      1. ``signal["asset_type"]`` if it explicitly says ``crypto``
      2. ``signal["lane"]`` override (caller-controlled)
      3. Symbol heuristic on ``signal["symbol"]``
      4. Default: ``"equity"`` (safer — adds the market-hours gate)
    """
    if not isinstance(signal, dict):
        return "equity"

    explicit_lane = signal.get("lane")
    if isinstance(explicit_lane, str):
        v = explicit_lane.lower()
        if v in ("crypto", "equity"):
            return v

    asset_type = (signal.get("asset_type") or "").lower()
    if asset_type == "crypto":
        return "crypto"
    # Anything else (stock, equity, etf, options) → equity lane.
    if asset_type in ("stock", "equity", "etf", "option", "options"):
        return "equity"

    symbol = (signal.get("symbol") or "").upper()
    if any(marker in symbol for marker in _CRYPTO_SYMBOL_MARKERS):
        return "crypto"

    return "equity"


def lane_config_for(lane: str) -> LaneConfig:
    """Resolve a lane name to its frozen config. Defaults to equity
    on any unknown value (safer fallback — has the market-hours gate)."""
    if lane == "crypto":
        return CRYPTO_LANE
    return EQUITY_LANE


def is_equity_market_open(now: Optional[Any] = None) -> bool:
    """Thin re-export of the canonical ``options_universe_service``
    market-hours helper so the executor doesn't have to import a
    sibling service. Single source of truth for "is the US session
    open right now?"."""
    from services.options_universe_service import is_market_open
    return is_market_open(now)
