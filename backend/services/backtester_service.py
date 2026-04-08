"""Strategy Backtester Service — simulates trading strategies against historical price data."""
import os
import logging
import asyncio
import requests
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _fetch_daily_prices(symbol: str, years: int = 3) -> List[Dict]:
    """Fetch daily historical prices from Alpha Vantage."""
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise ValueError("Alpha Vantage API key not configured")

    outputsize = "full"
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol.upper(),
        "outputsize": outputsize,
        "apikey": api_key,
    }
    resp = requests.get(url, params=params, timeout=30)
    data = resp.json()

    ts = data.get("Time Series (Daily)", {})
    if not ts:
        raise ValueError(f"No historical data found for {symbol}. Check if the ticker is valid.")

    cutoff = datetime.now().replace(year=datetime.now().year - years)
    prices = []
    for date_str, bar in sorted(ts.items()):
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt < cutoff:
            continue
        prices.append({
            "date": date_str,
            "open": float(bar["1. open"]),
            "high": float(bar["2. high"]),
            "low": float(bar["3. low"]),
            "close": float(bar["4. close"]),
            "volume": int(bar["5. volume"]),
        })
    return prices


# ── Technical Indicators ──

def _sma(closes: np.ndarray, period: int) -> np.ndarray:
    result = np.full_like(closes, np.nan)
    if len(closes) >= period:
        cumsum = np.cumsum(closes)
        cumsum[period:] = cumsum[period:] - cumsum[:-period]
        result[period - 1:] = cumsum[period - 1:] / period
    return result


def _ema(closes: np.ndarray, period: int) -> np.ndarray:
    result = np.full_like(closes, np.nan)
    if len(closes) < period:
        return result
    # Find first non-NaN index
    valid_start = 0
    for idx in range(len(closes)):
        if not np.isnan(closes[idx]):
            valid_start = idx
            break
    else:
        return result
    if len(closes) - valid_start < period:
        return result
    k = 2.0 / (period + 1)
    start = valid_start + period - 1
    result[start] = np.mean(closes[valid_start:valid_start + period])
    for i in range(start + 1, len(closes)):
        if np.isnan(closes[i]):
            continue
        result[i] = closes[i] * k + result[i - 1] * (1 - k)
    return result


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    result = np.full_like(closes, np.nan)
    if len(closes) < period + 1:
        return result
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return result


def _macd(closes: np.ndarray, fast=12, slow=26, signal=9):
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger(closes: np.ndarray, period=20, std_dev=2.0):
    mid = _sma(closes, period)
    rolling_std = np.full_like(closes, np.nan)
    for i in range(period - 1, len(closes)):
        rolling_std[i] = np.std(closes[i - period + 1:i + 1])
    upper = mid + std_dev * rolling_std
    lower = mid - std_dev * rolling_std
    return upper, mid, lower


def _compute_indicators(prices: List[Dict]) -> Dict[str, np.ndarray]:
    """Compute all common indicators from price data."""
    closes = np.array([p["close"] for p in prices])
    highs = np.array([p["high"] for p in prices])
    lows = np.array([p["low"] for p in prices])
    volumes = np.array([p["volume"] for p in prices], dtype=float)

    return {
        "close": closes,
        "high": highs,
        "low": lows,
        "volume": volumes,
        "sma_20": _sma(closes, 20),
        "sma_50": _sma(closes, 50),
        "sma_200": _sma(closes, 200),
        "ema_12": _ema(closes, 12),
        "ema_26": _ema(closes, 26),
        "rsi_14": _rsi(closes, 14),
        "macd_line": _macd(closes)[0],
        "macd_signal": _macd(closes)[1],
        "macd_hist": _macd(closes)[2],
        "bb_upper": _bollinger(closes)[0],
        "bb_mid": _bollinger(closes)[1],
        "bb_lower": _bollinger(closes)[2],
    }


# ── AI Rule Interpreter ──

async def _interpret_rules(api_key: str, strategy: Dict) -> Dict:
    """Use AI to convert strategy rules into evaluable conditions."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    rules_text = []
    for r in strategy.get("entry_rules", []):
        rules_text.append(f"ENTRY: {r.get('condition', '')}")
    for r in strategy.get("exit_rules", []):
        rules_text.append(f"EXIT: {r.get('condition', '')}")

    risk = strategy.get("risk_management", {})
    stop_loss = risk.get("stop_loss", "2% below entry")
    take_profit = risk.get("take_profit", "6% above entry")

    prompt = f"""Convert these trading rules into simple numeric conditions using these available indicators:
close, high, low, volume, sma_20, sma_50, sma_200, ema_12, ema_26, rsi_14, macd_line, macd_signal, macd_hist, bb_upper, bb_mid, bb_lower

Also extract: prev_close, prev_rsi_14, prev_macd_line, prev_macd_signal (previous bar values)

Rules:
{chr(10).join(rules_text)}

Stop loss: {stop_loss}
Take profit: {take_profit}

Return ONLY valid JSON (no markdown) with:
{{
  "entry_conditions": ["rsi_14 < 30", "macd_hist > prev_macd_hist"],
  "exit_conditions": ["rsi_14 > 70"],
  "stop_loss_pct": 2.0,
  "take_profit_pct": 6.0
}}

Use Python comparison operators. Reference indicator names exactly as listed. Use 'and'/'or' for compound conditions within a single string, or separate strings for AND logic."""

    session_id = f"bt_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    chat = LlmChat(
        api_key=api_key,
        session_id=session_id,
        system_message="You convert trading rules to numeric conditions. Return only JSON."
    ).with_model("openai", "gpt-5.2")

    response = await chat.send_message(UserMessage(text=prompt))
    import json
    text = str(response).strip()
    brace_start = text.find('{')
    brace_end = text.rfind('}')
    if brace_start >= 0 and brace_end > brace_start:
        text = text[brace_start:brace_end + 1]
    return json.loads(text)


def _eval_condition(cond: str, ctx: Dict) -> bool:
    """Safely evaluate a single indicator condition string."""
    try:
        safe_env = {}
        for k, v in ctx.items():
            if isinstance(v, (int, float, np.floating)):
                safe_env[k] = None if np.isnan(v) else float(v)
        # Only check variables that appear in this specific condition
        for k, v in safe_env.items():
            if k in cond and v is None:
                return False
        return bool(eval(cond, {"__builtins__": {}}, safe_env))
    except Exception:
        return False


# ── Simulation Engine ──

def _simulate(prices: List[Dict], indicators: Dict, rules: Dict) -> List[Dict]:
    """Run the backtest simulation and return a trade log."""
    entry_conds = rules.get("entry_conditions", [])
    exit_conds = rules.get("exit_conditions", [])
    sl_pct = rules.get("stop_loss_pct", 2.0) / 100.0
    tp_pct = rules.get("take_profit_pct", 6.0) / 100.0

    trades = []
    position = None  # {entry_price, entry_date, entry_idx}
    n = len(prices)

    for i in range(1, n):
        ctx = {
            "close": indicators["close"][i],
            "high": indicators["high"][i],
            "low": indicators["low"][i],
            "volume": indicators["volume"][i],
            "prev_close": indicators["close"][i - 1],
            "sma_20": indicators["sma_20"][i],
            "sma_50": indicators["sma_50"][i],
            "sma_200": indicators["sma_200"][i],
            "ema_12": indicators["ema_12"][i],
            "ema_26": indicators["ema_26"][i],
            "rsi_14": indicators["rsi_14"][i],
            "prev_rsi_14": indicators["rsi_14"][i - 1],
            "macd_line": indicators["macd_line"][i],
            "macd_signal": indicators["macd_signal"][i],
            "macd_hist": indicators["macd_hist"][i],
            "prev_macd_line": indicators["macd_line"][i - 1],
            "prev_macd_signal": indicators["macd_signal"][i - 1],
            "prev_macd_hist": indicators["macd_hist"][i - 1],
            "bb_upper": indicators["bb_upper"][i],
            "bb_mid": indicators["bb_mid"][i],
            "bb_lower": indicators["bb_lower"][i],
        }

        # Skip if any core indicator is NaN
        if np.isnan(ctx["rsi_14"]) or np.isnan(ctx["sma_20"]):
            continue

        if position is None:
            # Check entry conditions (all must be true)
            if entry_conds and all(_eval_condition(c, ctx) for c in entry_conds):
                position = {
                    "entry_price": prices[i]["close"],
                    "entry_date": prices[i]["date"],
                    "entry_idx": i,
                }
        else:
            price = prices[i]["close"]
            entry = position["entry_price"]
            pnl_pct = (price - entry) / entry

            exit_reason = None
            if pnl_pct <= -sl_pct:
                exit_reason = "stop_loss"
            elif pnl_pct >= tp_pct:
                exit_reason = "take_profit"
            elif exit_conds and all(_eval_condition(c, ctx) for c in exit_conds):
                exit_reason = "signal"

            if exit_reason:
                trades.append({
                    "entry_date": position["entry_date"],
                    "exit_date": prices[i]["date"],
                    "entry_price": round(entry, 2),
                    "exit_price": round(price, 2),
                    "pnl": round(price - entry, 2),
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "holding_days": i - position["entry_idx"],
                    "exit_reason": exit_reason,
                })
                position = None

    # Close any open position at last price
    if position:
        last = prices[-1]
        entry = position["entry_price"]
        pnl_pct = (last["close"] - entry) / entry
        trades.append({
            "entry_date": position["entry_date"],
            "exit_date": last["date"],
            "entry_price": round(entry, 2),
            "exit_price": round(last["close"], 2),
            "pnl": round(last["close"] - entry, 2),
            "pnl_pct": round(pnl_pct * 100, 2),
            "holding_days": len(prices) - 1 - position["entry_idx"],
            "exit_reason": "open",
        })

    return trades


# ── Metrics Calculator ──

def _calc_metrics(trades: List[Dict], prices: List[Dict]) -> Dict:
    """Calculate performance metrics from the trade log."""
    if not trades:
        bh_pnl = 0
        bh_pct = 0
        if prices:
            bh_start = prices[0]["close"]
            bh_end = prices[-1]["close"]
            bh_pnl = round(bh_end - bh_start, 2)
            bh_pct = round((bh_end - bh_start) / bh_start * 100, 2) if bh_start else 0
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0, "total_pnl": 0, "avg_pnl": 0, "avg_gain": 0, "avg_loss": 0,
            "max_drawdown": 0, "sharpe_ratio": 0, "avg_holding_days": 0,
            "best_trade": None, "worst_trade": None, "monthly": [],
            "cumulative_pnl": [], "buy_hold_pnl": bh_pnl, "buy_hold_pct": bh_pct,
        }

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    pnls = [t["pnl"] for t in trades]

    total_pnl = sum(pnls)
    avg_pnl = total_pnl / len(trades)
    win_rate = round(len(wins) / len(trades) * 100, 1)

    # Cumulative P&L
    cum_pnl = []
    running = 0.0
    for t in trades:
        running += t["pnl"]
        cum_pnl.append({"date": t["exit_date"], "pnl": round(running, 2)})

    # Max drawdown
    peak = 0.0
    max_dd = 0.0
    running = 0.0
    for t in trades:
        running += t["pnl"]
        peak = max(peak, running)
        dd = peak - running
        max_dd = max(max_dd, dd)

    # Sharpe ratio (annualized, assuming 252 trading days)
    if len(pnls) > 1:
        pnl_arr = np.array(pnls)
        sharpe = (np.mean(pnl_arr) / np.std(pnl_arr)) * np.sqrt(252 / max(1, np.mean([t["holding_days"] for t in trades]))) if np.std(pnl_arr) > 0 else 0
    else:
        sharpe = 0

    # Monthly breakdown
    monthly = {}
    for t in trades:
        month = t["exit_date"][:7]  # YYYY-MM
        if month not in monthly:
            monthly[month] = {"month": month, "trades": 0, "pnl": 0, "wins": 0}
        monthly[month]["trades"] += 1
        monthly[month]["pnl"] = round(monthly[month]["pnl"] + t["pnl"], 2)
        if t["pnl"] > 0:
            monthly[month]["wins"] += 1

    monthly_list = sorted(monthly.values(), key=lambda x: x["month"])

    # Buy & Hold comparison
    if prices:
        bh_start = prices[0]["close"]
        bh_end = prices[-1]["close"]
        bh_pnl = round(bh_end - bh_start, 2)
        bh_pct = round((bh_end - bh_start) / bh_start * 100, 2)
    else:
        bh_pnl = 0
        bh_pct = 0

    best = max(trades, key=lambda t: t["pnl"])
    worst = min(trades, key=lambda t: t["pnl"])

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(avg_pnl, 2),
        "avg_gain": round(np.mean([t["pnl"] for t in wins]), 2) if wins else 0,
        "avg_loss": round(np.mean([t["pnl"] for t in losses]), 2) if losses else 0,
        "max_drawdown": round(max_dd, 2),
        "sharpe_ratio": round(float(sharpe), 2),
        "avg_holding_days": round(np.mean([t["holding_days"] for t in trades]), 1),
        "best_trade": best,
        "worst_trade": worst,
        "buy_hold_pnl": bh_pnl,
        "buy_hold_pct": bh_pct,
        "monthly": monthly_list,
        "cumulative_pnl": cum_pnl,
    }


# ── Main Entry Point ──

async def run_backtest(api_key: str, strategy: Dict, symbol: str, years: int = 3) -> Dict:
    """Run a full backtest: fetch data, compute indicators, interpret rules, simulate, return metrics."""
    logger.info(f"Starting backtest for {symbol} ({years}y) with strategy: {strategy.get('name', 'unnamed')}")

    # 1. Fetch historical prices (run in thread to avoid blocking event loop)
    prices = await asyncio.to_thread(_fetch_daily_prices, symbol, years)
    if len(prices) < 50:
        raise ValueError(f"Insufficient historical data for {symbol} ({len(prices)} days). Need at least 50.")

    # 2. Compute indicators
    indicators = _compute_indicators(prices)

    # 3. Interpret rules via AI
    rules = await _interpret_rules(api_key, strategy)

    # 4. Simulate
    trades = _simulate(prices, indicators, rules)

    # 5. Calculate metrics
    metrics = _calc_metrics(trades, prices)

    return {
        "symbol": symbol.upper(),
        "strategy_name": strategy.get("name", "Unnamed Strategy"),
        "period": f"{years} year{'s' if years > 1 else ''}",
        "data_points": len(prices),
        "date_range": {"start": prices[0]["date"], "end": prices[-1]["date"]},
        "metrics": metrics,
        "trades": trades[-50:],  # Last 50 trades for the log
        "total_trades_generated": len(trades),
        "rules_interpreted": rules,
        "backtested_at": datetime.now(timezone.utc).isoformat(),
    }
