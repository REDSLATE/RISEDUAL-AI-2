"""Order Flow & Institutional Wall Detection — Market Microstructure Analysis.

Detects price levels where institutional players have positioned large orders
by analyzing intraday volume profiles. A "wall" is a price level with volume
significantly above the median — indicating institutional support/resistance.

Data source: yfinance intraday bars (5-minute intervals, 1-2 day lookback).

The output feeds directly into the AI crew synthesizer prompts as an
additional signal alongside Edge (win patterns) and Veto (toxic lessons).
"""

import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
from collections import defaultdict

import yfinance as yf
import numpy as np

logger = logging.getLogger(__name__)

# Wall detection thresholds
WALL_MULTIPLIER = 3.0          # Volume must be >3x median to qualify as a wall
SIGNIFICANT_WALL = 5.0         # >5x median = "major" wall
CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "DOT", "MATIC", "LINK"}


def _yf_symbol(ticker: str) -> str:
    """Convert to yfinance format."""
    upper = ticker.upper().replace("-USD", "")
    if upper in CRYPTO_TICKERS:
        return f"{upper}-USD"
    return upper


async def get_volume_profile(ticker: str, period: str = "2d", interval: str = "5m") -> Dict:
    """Build a volume-at-price profile from intraday data.

    Returns price levels bucketed with total volume traded at each level,
    plus detected institutional walls.
    """
    yf_sym = _yf_symbol(ticker)

    try:
        data = await asyncio.to_thread(
            lambda: yf.Ticker(yf_sym).history(period=period, interval=interval)
        )
    except Exception as e:
        logger.warning(f"Order flow data fetch failed for {ticker}: {e}")
        return {"error": str(e), "walls": [], "profile": []}

    if data is None or data.empty:
        return {"error": "No intraday data available", "walls": [], "profile": []}

    # Build volume profile: bucket prices and sum volume at each level
    prices = data["Close"].values
    volumes = data["Volume"].values
    highs = data["High"].values
    lows = data["Low"].values

    if len(prices) == 0:
        return {"error": "Empty price data", "walls": [], "profile": []}

    current_price = float(prices[-1])
    price_min, price_max = float(np.min(lows)), float(np.max(highs))
    price_range = price_max - price_min

    if price_range <= 0:
        return {"error": "No price movement", "walls": [], "profile": []}

    # Create price buckets (20-40 levels depending on range)
    num_buckets = min(40, max(20, int(price_range / (current_price * 0.002))))
    bucket_size = price_range / num_buckets
    buckets = defaultdict(float)

    for i in range(len(prices)):
        bucket_idx = int((float(prices[i]) - price_min) / bucket_size)
        bucket_idx = min(bucket_idx, num_buckets - 1)
        bucket_price = price_min + (bucket_idx + 0.5) * bucket_size
        buckets[round(bucket_price, 2)] += float(volumes[i])

    # Convert to sorted list
    profile = [{"price": p, "volume": int(v)} for p, v in sorted(buckets.items())]
    vol_values = [b["volume"] for b in profile]

    if not vol_values:
        return {"error": "Could not build profile", "walls": [], "profile": []}

    median_vol = float(np.median(vol_values))

    # Detect walls: price levels with volume significantly above median
    walls = []
    for bucket in profile:
        if median_vol <= 0:
            continue
        ratio = bucket["volume"] / median_vol
        if ratio >= WALL_MULTIPLIER:
            wall_type = "support" if bucket["price"] < current_price else "resistance"
            distance_pct = ((bucket["price"] - current_price) / current_price) * 100
            strength = "major" if ratio >= SIGNIFICANT_WALL else "minor"

            walls.append({
                "price": bucket["price"],
                "volume": bucket["volume"],
                "ratio": round(ratio, 1),
                "type": wall_type,
                "strength": strength,
                "distance_pct": round(distance_pct, 2),
            })

    # Sort walls by volume (strongest first)
    walls.sort(key=lambda w: w["volume"], reverse=True)

    # Compute summary metrics
    support_walls = [w for w in walls if w["type"] == "support"]
    resistance_walls = [w for w in walls if w["type"] == "resistance"]
    total_support_vol = sum(w["volume"] for w in support_walls)
    total_resistance_vol = sum(w["volume"] for w in resistance_walls)

    # Institutional bias: which side has more volume?
    if total_support_vol + total_resistance_vol > 0:
        support_ratio = total_support_vol / (total_support_vol + total_resistance_vol)
        if support_ratio > 0.65:
            bias = "INSTITUTIONAL_BID"
        elif support_ratio < 0.35:
            bias = "INSTITUTIONAL_ASK"
        else:
            bias = "BALANCED"
    else:
        bias = "NO_DATA"

    # Point of Control (POC): price level with highest volume
    poc = max(profile, key=lambda b: b["volume"]) if profile else None

    return {
        "ticker": ticker.upper(),
        "current_price": round(current_price, 2),
        "period": period,
        "interval": interval,
        "total_bars": len(prices),
        "price_range": {"low": round(price_min, 2), "high": round(price_max, 2)},
        "point_of_control": {
            "price": poc["price"] if poc else 0,
            "volume": poc["volume"] if poc else 0,
        },
        "walls": walls[:10],  # Top 10 walls
        "summary": {
            "support_walls": len(support_walls),
            "resistance_walls": len(resistance_walls),
            "total_support_volume": total_support_vol,
            "total_resistance_volume": total_resistance_vol,
            "bias": bias,
            "strongest_support": support_walls[0]["price"] if support_walls else None,
            "strongest_resistance": resistance_walls[0]["price"] if resistance_walls else None,
        },
        "profile": profile,
        "median_volume": int(median_vol),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_order_flow_context(ticker: str) -> str:
    """Generate a text summary of order flow for AI crew injection.

    Returns a formatted string that gets injected into the synthesizer prompt.
    """
    result = await get_volume_profile(ticker)

    if result.get("error") or not result.get("walls"):
        return ""

    walls = result["walls"]
    summary = result["summary"]
    poc = result.get("point_of_control", {})
    current = result.get("current_price", 0)

    lines = [f"ORDER FLOW ANALYSIS for {ticker} (Intraday Volume Profile):"]
    lines.append(f"Current Price: ${current:.2f} | Point of Control: ${poc.get('price', 0):.2f}")
    lines.append(f"Institutional Bias: {summary['bias']}")

    if summary.get("strongest_support"):
        lines.append(f"Strongest Support Wall: ${summary['strongest_support']:.2f}")
    if summary.get("strongest_resistance"):
        lines.append(f"Strongest Resistance Wall: ${summary['strongest_resistance']:.2f}")

    lines.append(f"\nDetected Institutional Walls ({len(walls)}):")
    for w in walls[:6]:
        emoji = "BID" if w["type"] == "support" else "ASK"
        lines.append(
            f"  [{emoji}] ${w['price']:.2f} — {w['volume']:,} vol ({w['ratio']}x median, "
            f"{w['strength']}, {w['distance_pct']:+.1f}% from current)"
        )

    # Trading implications
    lines.append("\nImplications:")
    if summary["bias"] == "INSTITUTIONAL_BID":
        lines.append("- Heavy institutional buying below current price → strong downside protection")
        lines.append("- Bullish signal: institutions are accumulating at support levels")
    elif summary["bias"] == "INSTITUTIONAL_ASK":
        lines.append("- Heavy institutional selling above current price → ceiling resistance")
        lines.append("- Bearish signal: institutions are distributing at resistance levels")
    else:
        lines.append("- Balanced flow: no clear institutional directional bias")

    if poc.get("price"):
        if poc["price"] < current:
            lines.append(f"- POC below price (${poc['price']:.2f}) suggests potential pullback magnet")
        else:
            lines.append(f"- POC above price (${poc['price']:.2f}) suggests upside attraction")

    return "\n".join(lines)
