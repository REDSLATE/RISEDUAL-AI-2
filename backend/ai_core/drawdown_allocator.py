"""Drawdown control and multi-bot capital allocator.

Pure-function risk layer that sits on top of the existing
`apply_portfolio_constraints` gate in :mod:`services.trading_bot_service`.
Two independent mechanisms, combined by :func:`apply_global_risk_controls`:

1. **Drawdown control** — tracks the fleet-wide equity curve and
   progressively shrinks per-trade risk as drawdown deepens. Flat
   between 0 and `SOFT_DRAWDOWN` (full risk), linear taper to
   `MIN_RISK_MULTIPLIER` over `SOFT_DRAWDOWN → MAX_DRAWDOWN`, and
   clamped to `MIN_RISK_MULTIPLIER` beyond that. We never go to
   zero — a 30% floor keeps bots generating signal-quality data
   even during a deep drawdown (critical for Tier 3 accuracy
   statistics to keep accumulating).

2. **Multi-bot allocator** — splits a total capital pool across
   several bots weighted by their recent performance (win-rate + a
   boolean PnL sign bonus). Losing bots keep a small floor
   (`0.1` minimum score) so they can rehabilitate.

All functions here are pure: no I/O, no global state, no DB. They
are called from :func:`services.trading_bot_service.execute_signal`
via the opt-in `equity_curve` / `bot_capital` kwargs and exercised
directly in `tests/test_drawdown_allocator.py`.
"""
from __future__ import annotations

# ────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────

# Drawdown thresholds — percentages expressed as 0.0-1.0 ratios.
MAX_DRAWDOWN: float = 0.20          # 20% — beyond this we clamp to the floor
SOFT_DRAWDOWN: float = 0.10         # 10% — risk taper begins here
MIN_RISK_MULTIPLIER: float = 0.3    # never cut risk below 30% of nominal

# Allocator defaults.
DEFAULT_BOT_WEIGHT: float = 1.0


# ────────────────────────────────────────────────────────────────────────────
# 1. Drawdown tracking
# ────────────────────────────────────────────────────────────────────────────

def compute_drawdown(equity_curve: list[float]) -> float:
    """Max drawdown seen across the equity curve, as a 0.0–1.0 ratio.

    Implementation walks the curve once tracking the running peak
    and the largest peak-to-trough gap. Returns 0.0 for an empty /
    monotonically-increasing curve.
    """
    if not equity_curve:
        return 0.0

    peak = equity_curve[0]
    max_dd = 0.0

    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd

    return round(max_dd, 4)


# ────────────────────────────────────────────────────────────────────────────
# 2. Drawdown → risk multiplier
# ────────────────────────────────────────────────────────────────────────────

def compute_drawdown_multiplier(equity_curve: list[float]) -> float:
    """Risk multiplier in ``[MIN_RISK_MULTIPLIER, 1.0]`` based on drawdown.

    Piecewise:
      * `dd ≤ SOFT_DRAWDOWN`       → 1.0  (full risk)
      * `SOFT_DRAWDOWN < dd < MAX_DRAWDOWN`
                                   → linear from 1.0 down to `MIN_RISK_MULTIPLIER`
      * `dd ≥ MAX_DRAWDOWN`        → `MIN_RISK_MULTIPLIER`
    """
    dd = compute_drawdown(equity_curve)

    if dd <= SOFT_DRAWDOWN:
        return 1.0

    if dd < MAX_DRAWDOWN:
        scale = 1.0 - ((dd - SOFT_DRAWDOWN) / (MAX_DRAWDOWN - SOFT_DRAWDOWN))
        return max(scale, MIN_RISK_MULTIPLIER)

    return MIN_RISK_MULTIPLIER


# ────────────────────────────────────────────────────────────────────────────
# 3. Per-bot performance score
# ────────────────────────────────────────────────────────────────────────────

def compute_bot_score(bot_stats: dict) -> float:
    """Score a bot from its stats dict. Higher = more capital.

    Expected keys:
      * ``win_rate``  — float in [0, 1]. Missing → 0.5 (neutral).
      * ``pnl``       — total realised profit. Missing → 0.

    Formula: ``0.7 * win_rate + (0.3 if pnl > 0 else 0)``. Floored
    at 0.1 so losing bots still get a sliver of capital to
    rehabilitate rather than getting starved to zero.
    """
    win_rate_raw = bot_stats.get("win_rate", 0.5)
    pnl_raw = bot_stats.get("pnl", 0)
    win_rate = 0.5 if win_rate_raw is None else float(win_rate_raw)
    pnl = 0.0 if pnl_raw is None else float(pnl_raw)

    score = (win_rate * 0.7) + (0.3 if pnl > 0 else 0.0)
    return max(score, 0.1)


# ────────────────────────────────────────────────────────────────────────────
# 4. Capital allocation across bots
# ────────────────────────────────────────────────────────────────────────────

def allocate_capital(total_capital: float, bots: dict[str, dict]) -> dict[str, float]:
    """Split ``total_capital`` across bots weighted by their scores.

    Empty or all-zero bot dict → returns an empty dict (caller
    should fall back to its own default). Otherwise returns
    ``{bot_name: usd_allocation}`` summing to ~``total_capital``
    (rounding can drop a cent or two).
    """
    if not bots:
        return {}

    scores: dict[str, float] = {}
    total_score = 0.0
    for name, stats in bots.items():
        score = compute_bot_score(stats)
        scores[name] = score
        total_score += score

    allocations: dict[str, float] = {}
    n = len(bots)
    for name, score in scores.items():
        weight = (score / total_score) if total_score > 0 else (1.0 / n)
        allocations[name] = round(total_capital * weight, 2)
    return allocations


# ────────────────────────────────────────────────────────────────────────────
# 5. Combine drawdown + allocation
# ────────────────────────────────────────────────────────────────────────────

def apply_global_risk_controls(
    base_size: float,
    equity_curve: list[float],
    bot_capital: float,
) -> float:
    """Combine per-bot capital cap and drawdown throttle on one trade.

      1. Clamp ``base_size`` to the per-bot capital envelope
         (``scaled = min(base_size, bot_capital)``).
      2. Multiply by :func:`compute_drawdown_multiplier` to taper
         risk during drawdowns.

    Returns a non-negative USD notional, rounded to cents.
    """
    scaled_size = min(float(base_size), float(bot_capital))
    if scaled_size <= 0:
        return 0.0
    dd_mult = compute_drawdown_multiplier(equity_curve)
    return round(scaled_size * dd_mult, 2)
