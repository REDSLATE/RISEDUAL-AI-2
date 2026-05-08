"""Strategy Backtester Service — simulates trading strategies against historical price data."""
import logging
import asyncio
import numpy as np
from datetime import datetime, timezone
from typing import Any, Optional

import ast

logger = logging.getLogger(__name__)


def _fetch_daily_prices(symbol: str, years: int = 3) -> list[dict]:
    """Fetch daily historical prices via smart price provider (AV -> yfinance)."""
    from services.price_provider import get_daily_history_sync

    history = get_daily_history_sync(symbol, "full")
    if not history:
        raise ValueError(f"No historical data found for {symbol}. Check if the ticker is valid.")

    # price_provider returns newest-first; reverse to oldest-first
    history = list(reversed(history))

    cutoff = datetime.now().replace(year=datetime.now().year - years)
    prices = []
    for bar in history:
        dt = datetime.strptime(bar["date"], "%Y-%m-%d")
        if dt < cutoff:
            continue
        prices.append(bar)
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


def _macd(
    closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger(
    closes: np.ndarray, period: int = 20, std_dev: float = 2.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = _sma(closes, period)
    rolling_std = np.full_like(closes, np.nan)
    for i in range(period - 1, len(closes)):
        rolling_std[i] = np.std(closes[i - period + 1:i + 1])
    upper = mid + std_dev * rolling_std
    lower = mid - std_dev * rolling_std
    return upper, mid, lower


def _compute_indicators(prices: list[dict]) -> dict[str, np.ndarray]:
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

async def _interpret_rules(api_key: str, strategy: dict) -> dict:
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


import operator

# ── Safe Expression Evaluator (replaces eval()) ──

_ALLOWED_INDICATORS = frozenset({
    'close', 'high', 'low', 'volume', 'prev_close',
    'sma_20', 'sma_50', 'sma_200', 'ema_12', 'ema_26',
    'rsi_14', 'prev_rsi_14',
    'macd_line', 'macd_signal', 'macd_hist',
    'prev_macd_line', 'prev_macd_signal', 'prev_macd_hist',
    'bb_upper', 'bb_mid', 'bb_lower',
})

_CMP_OPS = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_BOOL_OPS = {
    ast.And: all,
    ast.Or: any,
}


def _safe_eval_node(node: ast.AST, ctx: dict) -> Any:
    """Recursively evaluate an AST node using only whitelisted operations."""
    _NODE_HANDLERS = {
        ast.Expression: lambda n, c: _safe_eval_node(n.body, c),
        ast.Constant: lambda n, c: _handle_constant(n),
        ast.Name: lambda n, c: _handle_name(n, c),
        ast.UnaryOp: lambda n, c: _handle_unary(n, c),
        ast.BinOp: lambda n, c: _handle_binop(n, c),
        ast.Compare: lambda n, c: _handle_compare(n, c),
        ast.BoolOp: lambda n, c: _handle_boolop(n, c),
    }
    handler = _NODE_HANDLERS.get(type(node))
    if not handler:
        raise ValueError(f"Unsupported AST node: {type(node).__name__}")
    return handler(node, ctx)


def _handle_constant(node: ast.Constant) -> float:
    if isinstance(node.value, (int, float)):
        return node.value
    raise ValueError(f"Unsupported constant: {node.value!r}")


def _handle_name(node: ast.Name, ctx: dict) -> Any:
    name = node.id
    if name not in _ALLOWED_INDICATORS:
        raise ValueError(f"Unknown indicator: {name}")
    val = ctx.get(name)
    if val is None:
        raise ValueError(f"Indicator {name} is NaN/missing")
    return val


def _handle_unary(node: ast.UnaryOp, ctx: dict) -> Any:
    if isinstance(node.op, ast.USub):
        return -_safe_eval_node(node.operand, ctx)
    raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")


def _handle_binop(node: ast.BinOp, ctx: dict) -> Any:
    op_func = _BIN_OPS.get(type(node.op))
    if not op_func:
        raise ValueError(f"Unsupported binary op: {type(node.op).__name__}")
    return op_func(_safe_eval_node(node.left, ctx), _safe_eval_node(node.right, ctx))


def _handle_compare(node: ast.Compare, ctx: dict) -> bool:
    left = _safe_eval_node(node.left, ctx)
    for op_node, comparator in zip(node.ops, node.comparators):
        op_func = _CMP_OPS.get(type(op_node))
        if not op_func:
            raise ValueError(f"Unsupported comparison: {type(op_node).__name__}")
        right = _safe_eval_node(comparator, ctx)
        if not op_func(left, right):
            return False
        left = right
    return True


def _handle_boolop(node: ast.BoolOp, ctx: dict) -> bool:
    func = _BOOL_OPS.get(type(node.op))
    if not func:
        raise ValueError(f"Unsupported bool op: {type(node.op).__name__}")
    return func(_safe_eval_node(v, ctx) for v in node.values)


def _eval_condition(cond: str, ctx: dict) -> bool:
    """Safely evaluate a trading condition string using AST parsing.

    Only allows: numeric literals, whitelisted indicator names,
    comparisons (<, <=, >, >=, ==, !=), arithmetic (+, -, *, /),
    and boolean operators (and, or).
    No dangerous builtins, no function calls, no attribute access.
    """
    if not cond or not isinstance(cond, str):
        return False
    # Build a safe numeric context (convert NaN to None for early rejection)
    safe_ctx = {}
    for k, v in ctx.items():
        if isinstance(v, (int, float, np.floating)):
            safe_ctx[k] = None if np.isnan(v) else float(v)
    try:
        tree = ast.parse(cond.strip(), mode='eval')
        return bool(_safe_eval_node(tree, safe_ctx))
    except Exception:
        return False


# ── Simulation Engine ──

def _build_bar_context(indicators: dict, i: int) -> dict:
    """Build the indicator context dict for bar index i."""
    return {
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


def _check_exit(price: float, entry: float, sl_pct: float, tp_pct: float,
                exit_conds: list[str], ctx: dict) -> Optional[str]:
    """Return exit reason string or None if position should stay open."""
    pnl_pct = (price - entry) / entry
    if pnl_pct <= -sl_pct:
        return "stop_loss"
    if pnl_pct >= tp_pct:
        return "take_profit"
    if exit_conds and all(_eval_condition(c, ctx) for c in exit_conds):
        return "signal"
    return None


def _record_trade(position: dict, exit_price: float, exit_date: str,
                  exit_idx: int, exit_reason: str) -> dict:
    """Create a trade record from a position and exit info."""
    entry = position["entry_price"]
    pnl_pct = (exit_price - entry) / entry
    return {
        "entry_date": position["entry_date"],
        "exit_date": exit_date,
        "entry_price": round(entry, 2),
        "exit_price": round(exit_price, 2),
        "pnl": round(exit_price - entry, 2),
        "pnl_pct": round(pnl_pct * 100, 2),
        "holding_days": exit_idx - position["entry_idx"],
        "exit_reason": exit_reason,
    }


def _simulate(prices: list[dict], indicators: dict, rules: dict) -> list[dict]:
    """Run the backtest simulation and return a trade log."""
    entry_conds = rules.get("entry_conditions", [])
    exit_conds = rules.get("exit_conditions", [])
    sl_pct = rules.get("stop_loss_pct", 2.0) / 100.0
    tp_pct = rules.get("take_profit_pct", 6.0) / 100.0

    trades = []
    position = None
    n = len(prices)

    for i in range(1, n):
        ctx = _build_bar_context(indicators, i)
        if np.isnan(ctx["rsi_14"]) or np.isnan(ctx["sma_20"]):
            continue

        if position is None:
            if entry_conds and all(_eval_condition(c, ctx) for c in entry_conds):
                position = {"entry_price": prices[i]["close"], "entry_date": prices[i]["date"], "entry_idx": i}
        else:
            reason = _check_exit(prices[i]["close"], position["entry_price"], sl_pct, tp_pct, exit_conds, ctx)
            if reason:
                trades.append(_record_trade(position, prices[i]["close"], prices[i]["date"], i, reason))
                position = None

    # Close any open position at last price
    if position:
        last = prices[-1]
        trades.append(_record_trade(position, last["close"], last["date"], len(prices) - 1, "open"))

    return trades


# ── Metrics Calculator ──

def _calc_cumulative_pnl(trades: list[dict]) -> list[dict]:
    """Calculate cumulative P&L series from trades."""
    cum_pnl = []
    running = 0.0
    for t in trades:
        running += t["pnl"]
        cum_pnl.append({"date": t["exit_date"], "pnl": round(running, 2)})
    return cum_pnl


def _calc_max_drawdown(trades: list[dict]) -> float:
    """Calculate maximum drawdown from peak P&L."""
    peak = 0.0
    max_dd = 0.0
    running = 0.0
    for t in trades:
        running += t["pnl"]
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return max_dd


def _calc_monthly_breakdown(trades: list[dict]) -> list[dict]:
    """Aggregate trades into monthly buckets."""
    monthly = {}
    for t in trades:
        month = t["exit_date"][:7]
        if month not in monthly:
            monthly[month] = {"month": month, "trades": 0, "pnl": 0, "wins": 0}
        monthly[month]["trades"] += 1
        monthly[month]["pnl"] = round(monthly[month]["pnl"] + t["pnl"], 2)
        if t["pnl"] > 0:
            monthly[month]["wins"] += 1
    return sorted(monthly.values(), key=lambda x: x["month"])


def _calc_buy_hold(prices: list[dict]) -> tuple:
    """Calculate buy & hold return."""
    if not prices:
        return 0, 0
    start = prices[0]["close"]
    end = prices[-1]["close"]
    pnl = round(end - start, 2)
    pct = round((end - start) / start * 100, 2) if start else 0
    return pnl, pct


def _calc_metrics(trades: list[dict], prices: list[dict]) -> dict:
    """Calculate performance metrics from the trade log."""
    bh_pnl, bh_pct = _calc_buy_hold(prices)

    if not trades:
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

    # Sharpe ratio (annualized)
    if len(pnls) > 1:
        pnl_arr = np.array(pnls)
        avg_hold = max(1, np.mean([t["holding_days"] for t in trades]))
        sharpe = (np.mean(pnl_arr) / np.std(pnl_arr)) * np.sqrt(252 / avg_hold) if np.std(pnl_arr) > 0 else 0
    else:
        sharpe = 0

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(trades), 2),
        "avg_gain": round(np.mean([t["pnl"] for t in wins]), 2) if wins else 0,
        "avg_loss": round(np.mean([t["pnl"] for t in losses]), 2) if losses else 0,
        "max_drawdown": round(_calc_max_drawdown(trades), 2),
        "sharpe_ratio": round(float(sharpe), 2),
        "avg_holding_days": round(np.mean([t["holding_days"] for t in trades]), 1),
        "best_trade": max(trades, key=lambda t: t["pnl"]),
        "worst_trade": min(trades, key=lambda t: t["pnl"]),
        "buy_hold_pnl": bh_pnl,
        "buy_hold_pct": bh_pct,
        "monthly": _calc_monthly_breakdown(trades),
        "cumulative_pnl": _calc_cumulative_pnl(trades),
    }


# ── Main Entry Point ──

async def run_backtest(api_key: str, strategy: dict, symbol: str, years: int = 3) -> dict:
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
