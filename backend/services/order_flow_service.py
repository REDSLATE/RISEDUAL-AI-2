"""Order Flow & Institutional Wall Detection — Market Microstructure Analysis.

Data Sources:
  - Crypto: Binance L2 Order Book (GET /api/v3/depth) — real bid/ask depth
  - Stocks: yfinance intraday volume profile (5-min bars, 2-day lookback)

A "wall" is a price level with volume significantly above the median,
indicating institutional support (bid wall) or resistance (ask wall).

The output feeds into all 3 AI crew synthesizer prompts alongside Edge/Veto.
"""

import logging
import asyncio
import requests
from datetime import datetime, timezone
from typing import Dict
from collections import defaultdict

import yfinance as yf
import numpy as np

logger = logging.getLogger(__name__)

WALL_MULTIPLIER = 3.0
SIGNIFICANT_WALL = 5.0
CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "DOT", "MATIC", "LINK"}

BINANCE_ENDPOINTS = [
    {"depth": "https://api.binance.us/api/v3/depth", "price": "https://api.binance.us/api/v3/ticker/price"},
    {"depth": "https://api.binance.com/api/v3/depth", "price": "https://api.binance.com/api/v3/ticker/price"},
]


def _is_crypto(ticker: str) -> bool:
    return ticker.upper().replace("-USD", "").replace("USDT", "") in CRYPTO_TICKERS


def _binance_symbol(ticker: str) -> str:
    """Convert to Binance pair format (e.g., BTC -> BTCUSDT)."""
    clean = ticker.upper().replace("-USD", "").replace("USDT", "")
    return f"{clean}USDT"


def _yf_symbol(ticker: str) -> str:
    upper = ticker.upper().replace("-USD", "")
    if upper in CRYPTO_TICKERS:
        return f"{upper}-USD"
    return upper


# ─────────────────────────────────────────────────────────
# BINANCE L2 ORDER BOOK (Crypto)
# ─────────────────────────────────────────────────────────

async def _fetch_binance_depth(ticker: str, limit: int = 500) -> Dict:
    """Fetch Binance L2 order book and detect institutional walls from real bids/asks.
    Tries Binance US first, then Binance global as fallback."""
    symbol = _binance_symbol(ticker)

    depth_data = None
    price_data = None

    for ep in BINANCE_ENDPOINTS:
        try:
            depth_resp, price_resp = await asyncio.gather(
                asyncio.to_thread(
                    lambda url=ep["depth"]: requests.get(url, params={"symbol": symbol, "limit": limit}, timeout=10)
                ),
                asyncio.to_thread(
                    lambda url=ep["price"]: requests.get(url, params={"symbol": symbol}, timeout=5)
                ),
            )
            d = depth_resp.json()
            p = price_resp.json()
            # Check for error responses (geo-block returns {"code": 0, "msg": "..."})
            if "code" in d or not d.get("bids"):
                logger.info(f"Binance endpoint {ep['depth']} blocked/empty for {symbol}, trying next")
                continue
            depth_data = d
            price_data = p
            break
        except Exception as e:
            logger.info(f"Binance endpoint {ep['depth']} failed for {symbol}: {e}")
            continue

    if not depth_data or not price_data:
        return {"error": "All Binance endpoints unavailable", "walls": [], "source": "binance"}

    bids = depth_data.get("bids", [])
    asks = depth_data.get("asks", [])
    current_price = float(price_data.get("price", 0))

    if not bids or not asks or current_price <= 0:
        return {"error": "No order book data", "walls": [], "source": "binance"}

    # Parse bid/ask levels: [[price, quantity], ...]
    bid_levels = [{"price": float(b[0]), "volume": float(b[0]) * float(b[1]), "qty": float(b[1])} for b in bids]
    ask_levels = [{"price": float(a[0]), "volume": float(a[0]) * float(a[1]), "qty": float(a[1])} for a in asks]

    all_volumes = [lv["volume"] for lv in bid_levels + ask_levels]
    median_vol = float(np.median(all_volumes)) if all_volumes else 0

    if median_vol <= 0:
        return {"error": "Zero median volume", "walls": [], "source": "binance"}

    # Detect walls from bids (support) and asks (resistance)
    walls = []

    for level in bid_levels:
        ratio = level["volume"] / median_vol
        if ratio >= WALL_MULTIPLIER:
            distance_pct = ((level["price"] - current_price) / current_price) * 100
            walls.append({
                "price": round(level["price"], 2),
                "volume": round(level["volume"], 2),
                "quantity": round(level["qty"], 4),
                "ratio": round(ratio, 1),
                "type": "support",
                "strength": "major" if ratio >= SIGNIFICANT_WALL else "minor",
                "distance_pct": round(distance_pct, 2),
            })

    for level in ask_levels:
        ratio = level["volume"] / median_vol
        if ratio >= WALL_MULTIPLIER:
            distance_pct = ((level["price"] - current_price) / current_price) * 100
            walls.append({
                "price": round(level["price"], 2),
                "volume": round(level["volume"], 2),
                "quantity": round(level["qty"], 4),
                "ratio": round(ratio, 1),
                "type": "resistance",
                "strength": "major" if ratio >= SIGNIFICANT_WALL else "minor",
                "distance_pct": round(distance_pct, 2),
            })

    walls.sort(key=lambda w: w["volume"], reverse=True)

    # Summary
    support_walls = [w for w in walls if w["type"] == "support"]
    resistance_walls = [w for w in walls if w["type"] == "resistance"]
    total_bid_vol = sum(lv["volume"] for lv in bid_levels)
    total_ask_vol = sum(lv["volume"] for lv in ask_levels)

    if total_bid_vol + total_ask_vol > 0:
        bid_ratio = total_bid_vol / (total_bid_vol + total_ask_vol)
        if bid_ratio > 0.58:
            bias = "INSTITUTIONAL_BID"
        elif bid_ratio < 0.42:
            bias = "INSTITUTIONAL_ASK"
        else:
            bias = "BALANCED"
    else:
        bias = "NO_DATA"

    # Best bid/ask spread
    best_bid = bid_levels[0]["price"] if bid_levels else 0
    best_ask = ask_levels[0]["price"] if ask_levels else 0
    spread = round(best_ask - best_bid, 2) if best_bid and best_ask else 0
    spread_pct = round((spread / current_price) * 100, 4) if current_price > 0 else 0

    return {
        "ticker": ticker.upper(),
        "current_price": round(current_price, 2),
        "source": "binance_l2",
        "depth_levels": limit,
        "total_bids": len(bids),
        "total_asks": len(asks),
        "spread": {"value": spread, "pct": spread_pct, "best_bid": round(best_bid, 2), "best_ask": round(best_ask, 2)},
        "walls": walls[:12],
        "summary": {
            "support_walls": len(support_walls),
            "resistance_walls": len(resistance_walls),
            "total_bid_volume": round(total_bid_vol, 2),
            "total_ask_volume": round(total_ask_vol, 2),
            "bid_ask_ratio": round(total_bid_vol / total_ask_vol, 2) if total_ask_vol > 0 else 0,
            "bias": bias,
            "strongest_support": support_walls[0]["price"] if support_walls else None,
            "strongest_resistance": resistance_walls[0]["price"] if resistance_walls else None,
        },
        "median_volume": round(median_vol, 2),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────
# YFINANCE VOLUME PROFILE (Stocks)
# ─────────────────────────────────────────────────────────

async def _fetch_yf_profile(ticker: str, period: str = "2d", interval: str = "5m") -> Dict:
    """Build volume-at-price profile from yfinance intraday data (stocks only)."""
    yf_sym = _yf_symbol(ticker)

    try:
        data = await asyncio.to_thread(
            lambda: yf.Ticker(yf_sym).history(period=period, interval=interval)
        )
    except Exception as e:
        logger.warning(f"yfinance order flow error for {ticker}: {e}")
        return {"error": str(e), "walls": [], "source": "yfinance"}

    if data is None or data.empty:
        return {"error": "No intraday data", "walls": [], "source": "yfinance"}

    prices = data["Close"].values
    volumes = data["Volume"].values
    highs = data["High"].values
    lows = data["Low"].values

    if len(prices) == 0:
        return {"error": "Empty price data", "walls": [], "source": "yfinance"}

    current_price = float(prices[-1])
    price_min, price_max = float(np.min(lows)), float(np.max(highs))
    price_range = price_max - price_min

    if price_range <= 0:
        return {"error": "No price movement", "walls": [], "source": "yfinance"}

    num_buckets = min(40, max(20, int(price_range / (current_price * 0.002))))
    bucket_size = price_range / num_buckets
    buckets = defaultdict(float)

    for i in range(len(prices)):
        bucket_idx = int((float(prices[i]) - price_min) / bucket_size)
        bucket_idx = min(bucket_idx, num_buckets - 1)
        bucket_price = price_min + (bucket_idx + 0.5) * bucket_size
        buckets[round(bucket_price, 2)] += float(volumes[i])

    profile = [{"price": p, "volume": int(v)} for p, v in sorted(buckets.items())]
    vol_values = [b["volume"] for b in profile]

    if not vol_values:
        return {"error": "Could not build profile", "walls": [], "source": "yfinance"}

    median_vol = float(np.median(vol_values))

    walls = []
    for bucket in profile:
        if median_vol <= 0:
            continue
        ratio = bucket["volume"] / median_vol
        if ratio >= WALL_MULTIPLIER:
            wall_type = "support" if bucket["price"] < current_price else "resistance"
            distance_pct = ((bucket["price"] - current_price) / current_price) * 100
            walls.append({
                "price": bucket["price"],
                "volume": bucket["volume"],
                "ratio": round(ratio, 1),
                "type": wall_type,
                "strength": "major" if ratio >= SIGNIFICANT_WALL else "minor",
                "distance_pct": round(distance_pct, 2),
            })

    walls.sort(key=lambda w: w["volume"], reverse=True)

    support_walls = [w for w in walls if w["type"] == "support"]
    resistance_walls = [w for w in walls if w["type"] == "resistance"]
    total_support_vol = sum(w["volume"] for w in support_walls)
    total_resistance_vol = sum(w["volume"] for w in resistance_walls)

    if total_support_vol + total_resistance_vol > 0:
        support_ratio = total_support_vol / (total_support_vol + total_resistance_vol)
        bias = "INSTITUTIONAL_BID" if support_ratio > 0.65 else ("INSTITUTIONAL_ASK" if support_ratio < 0.35 else "BALANCED")
    else:
        bias = "NO_DATA"

    poc = max(profile, key=lambda b: b["volume"]) if profile else None

    return {
        "ticker": ticker.upper(),
        "current_price": round(current_price, 2),
        "source": "yfinance_profile",
        "period": period,
        "interval": interval,
        "total_bars": len(prices),
        "price_range": {"low": round(price_min, 2), "high": round(price_max, 2)},
        "point_of_control": {"price": poc["price"] if poc else 0, "volume": poc["volume"] if poc else 0},
        "walls": walls[:10],
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


# ─────────────────────────────────────────────────────────
# PUBLIC API — Auto-routes to Binance (crypto) or yfinance (stocks)
# ─────────────────────────────────────────────────────────

async def get_volume_profile(ticker: str, **kwargs) -> Dict:
    """Smart router: Binance L2 for crypto, yfinance for stocks."""
    if _is_crypto(ticker):
        result = await _fetch_binance_depth(ticker)
        if not result.get("error"):
            return result
        # Fallback to yfinance if Binance fails
        logger.info(f"Binance L2 failed for {ticker}, falling back to yfinance")

    return await _fetch_yf_profile(ticker, **kwargs)


async def get_order_flow_context(ticker: str) -> str:
    """Generate text summary of order flow for AI crew injection."""
    result = await get_volume_profile(ticker)

    if result.get("error") or not result.get("walls"):
        return ""

    walls = result["walls"]
    summary = result["summary"]
    current = result.get("current_price", 0)
    source = result.get("source", "unknown")

    source_label = "Binance L2 Order Book" if "binance" in source else "Intraday Volume Profile"
    lines = [f"ORDER FLOW ANALYSIS for {ticker} ({source_label}):"]
    lines.append(f"Current Price: ${current:.2f}")
    lines.append(f"Institutional Bias: {summary['bias']}")

    # Binance-specific: spread info
    spread = result.get("spread")
    if spread and spread.get("pct"):
        lines.append(f"Spread: ${spread['value']} ({spread['pct']:.4f}%) | Best Bid: ${spread['best_bid']} | Best Ask: ${spread['best_ask']}")

    # Binance-specific: bid/ask ratio
    if summary.get("bid_ask_ratio"):
        lines.append(f"Bid/Ask Volume Ratio: {summary['bid_ask_ratio']}x")

    # POC (yfinance only)
    poc = result.get("point_of_control")
    if poc and poc.get("price"):
        lines.append(f"Point of Control: ${poc['price']:.2f}")

    if summary.get("strongest_support"):
        lines.append(f"Strongest Support Wall: ${summary['strongest_support']:.2f}")
    if summary.get("strongest_resistance"):
        lines.append(f"Strongest Resistance Wall: ${summary['strongest_resistance']:.2f}")

    lines.append(f"\nDetected Institutional Walls ({len(walls)}):")
    for w in walls[:6]:
        side = "BID" if w["type"] == "support" else "ASK"
        qty_str = f" ({w['quantity']:.4f} coins)" if w.get("quantity") else ""
        lines.append(
            f"  [{side}] ${w['price']:.2f} — ${w['volume']:,.0f} vol{qty_str} "
            f"({w['ratio']}x median, {w['strength']}, {w['distance_pct']:+.1f}% from current)"
        )

    lines.append("\nImplications:")
    if summary["bias"] == "INSTITUTIONAL_BID":
        lines.append("- Heavy institutional buying below current price — strong downside protection")
        lines.append("- Bullish signal: institutions are accumulating at support levels")
    elif summary["bias"] == "INSTITUTIONAL_ASK":
        lines.append("- Heavy institutional selling above current price — ceiling resistance")
        lines.append("- Bearish signal: institutions are distributing at resistance levels")
    else:
        lines.append("- Balanced flow: no clear institutional directional bias")

    if spread and spread.get("pct", 0) > 0.1:
        lines.append(f"- Wide spread ({spread['pct']:.2f}%) indicates low liquidity — be cautious with large orders")

    if poc and poc.get("price"):
        if poc["price"] < current:
            lines.append(f"- POC below price (${poc['price']:.2f}) suggests potential pullback magnet")
        else:
            lines.append(f"- POC above price (${poc['price']:.2f}) suggests upside attraction")

    return "\n".join(lines)
