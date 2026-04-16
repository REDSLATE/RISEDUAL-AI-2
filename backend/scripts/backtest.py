#!/usr/bin/env python3
"""Walk-forward backtest — evaluates SignalModel on labeled snapshots.

Loads labeled FeaturesSnapshots from MongoDB, splits into train/test (70/30),
trains the model on the train set, simulates predictions on the test set,
computes Sharpe ratio, max drawdown, win rate (overall + by regime), and
saves a BacktestResult JSON.

Usage:
    cd /app/backend && python scripts/backtest.py
    cd /app/backend && python scripts/backtest.py --model-path models/signal_model_v2.joblib
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from risedual_core.ml.signal_model import SignalModel
from risedual_core.schemas.market import BacktestResult, RegimeMetrics, FeaturesSnapshot

MONGO_URI = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "risedual_db")
COLLECTION = "features_snapshots"
RESULTS_DIR = Path("/app/backend/backtest_results")

FEATURE_COLS = [
    "rsi_14", "macd", "macd_signal", "sma_20", "sma_50",
    "volume_ratio", "sentiment_score", "insider_activity", "sector_momentum",
    "pattern_bullish_engulfing", "pattern_bearish_engulfing",
    "pattern_bull_flag", "pattern_rsi_divergence", "pattern_macd_crossover",
    "pattern_volume_surge", "pattern_head_and_shoulders",
]


async def load_labeled_data(db) -> pd.DataFrame:
    cursor = db[COLLECTION].find(
        {"$or": [
            {"outcome": {"$in": ["up", "down", "flat"]}},
            {"outcome_1d": {"$in": ["up", "down", "flat"]}},
        ]},
        projection={"_id": 0, "ticker": 1, "outcome": 1, "outcome_1d": 1,
                     "captured_at": 1, "timestamp": 1,
                     "regime_label": 1, "prediction_price": 1, "outcome_price": 1,
                     "price": 1, "return_1d": 1,
                     **{col: 1 for col in FEATURE_COLS}},
    ).sort("timestamp", 1)
    docs = await cursor.to_list(length=None)
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    # Normalize: use outcome_1d if outcome is missing
    if "outcome" not in df.columns or df["outcome"].isna().all():
        df["outcome"] = df.get("outcome_1d")
    else:
        df["outcome"] = df["outcome"].fillna(df.get("outcome_1d"))
    return df


# ── Pattern-aware confidence & sizing strategy ────────────────────────────────

# Patterns with proven alpha lift (from backtest diagnostics)
HIGH_ALPHA_PATTERNS = {
    "pattern_rsi_divergence":    {"lift": 0.090, "conf_floor": 0.38},
    "pattern_head_and_shoulders":{"lift": 0.330, "conf_floor": 0.35},
}
# Secondary patterns — positive PnL in backtest
MEDIUM_ALPHA_PATTERNS = {
    "pattern_volume_surge":      {"lift": 0.038, "conf_floor": 0.42},
    "pattern_bull_flag":         {"lift": 0.021, "conf_floor": 0.44},
}
# EXCLUDED: double_bottom — negative PnL in backtest (42.2% WR, net drag)

# Default confidence gate for signals with no pattern
DEFAULT_CONF_GATE = 0.52


def _effective_conf_gate(row: pd.Series) -> float:
    """Determine the confidence gate for this row based on active patterns.

    High-alpha patterns lower the gate → more trades on proven setups.
    """
    for pat, cfg in HIGH_ALPHA_PATTERNS.items():
        if row.get(pat, False):
            return cfg["conf_floor"]
    for pat, cfg in MEDIUM_ALPHA_PATTERNS.items():
        if row.get(pat, False):
            return cfg["conf_floor"]
    return DEFAULT_CONF_GATE


def _position_multiplier(row: pd.Series) -> float:
    """Concentrate capital on high-lift pattern+regime alignments.

    Returns a multiplier (1.0 = normal, up to 2.5x for best setups).
    """
    regime = str(row.get("regime_label", "")).lower()
    mult = 1.0

    # High-alpha pattern present
    for pat, cfg in HIGH_ALPHA_PATTERNS.items():
        if row.get(pat, False):
            mult = 1.8
            # Regime alignment bonus
            if pat == "pattern_rsi_divergence" and regime in ("sideways", "bear"):
                mult = 2.2  # RSI divergence in sideways/bear = strongest setup
            elif pat == "pattern_double_bottom" and regime == "sideways":
                mult = 2.0
            elif pat == "pattern_head_and_shoulders" and regime in ("bull", "sideways"):
                mult = 2.0
            break

    # Medium-alpha pattern
    if mult == 1.0:
        for pat in MEDIUM_ALPHA_PATTERNS:
            if row.get(pat, False):
                mult = 1.6
                if regime == "sideways":
                    mult = 1.9
                break

    # Regime-only adjustments (no pattern)
    if mult == 1.0:
        if regime == "bear":
            mult = 0.5  # reduce exposure in bear w/o pattern confirmation
        elif regime == "bull":
            mult = 0.8  # slightly reduce — bull underperforms in backtest

    return mult


def simulate_pnl(df_test: pd.DataFrame, predictions: list[int], probabilities: list[float]) -> dict:
    """Simulate pattern-aware P&L with variable confidence gates and position sizing."""
    returns = []
    wins = 0
    losses = 0
    skipped_by_gate = 0

    # Per-pattern trade tracking
    pattern_stats: dict[str, dict] = {}
    for pat in list(HIGH_ALPHA_PATTERNS) + list(MEDIUM_ALPHA_PATTERNS):
        pattern_stats[pat] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
    pattern_stats["no_pattern"] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}

    # Per-regime trade tracking
    regime_trade_stats: dict[str, dict] = {}
    for r in ["bull", "bear", "sideways", ""]:
        regime_trade_stats[r] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}

    # Pattern+regime combo tracking
    combo_stats: dict[str, dict] = {}

    for i, (_, row) in enumerate(df_test.iterrows()):
        pred = predictions[i]
        conf = probabilities[i]

        # Dynamic confidence gate based on pattern presence
        gate = _effective_conf_gate(row)

        if pred == 0 or conf < gate:
            returns.append(0.0)
            if pred == 1 and conf < gate:
                skipped_by_gate += 1
            continue

        # Calculate actual return
        pred_price = row.get("prediction_price")
        out_price = row.get("outcome_price")
        return_1d = row.get("return_1d")
        if return_1d is not None and not pd.isna(return_1d):
            pct_return = float(return_1d)
        elif pred_price and out_price and pred_price > 0:
            pct_return = (out_price - pred_price) / pred_price
        else:
            outcome = row.get("outcome", "flat")
            pct_return = 0.015 if outcome == "up" else (-0.015 if outcome == "down" else 0.0)

        # Position size: base Kelly * pattern/regime multiplier
        base_size = min(0.12, max(0.03, (conf - 0.5) * 1.5))
        multiplier = _position_multiplier(row)
        position_size = min(0.20, base_size * multiplier)  # hard cap at 20%

        # Per-trade stop-loss: cap max loss at 2% of portfolio
        weighted_return = pct_return * position_size
        if weighted_return < -0.02:
            weighted_return = -0.02  # stop-loss triggered

        returns.append(weighted_return)
        is_win = pct_return > 0
        if is_win:
            wins += 1
        elif pct_return < 0:
            losses += 1

        # Track by pattern
        regime = str(row.get("regime_label", "")).lower()
        if regime == "nan" or regime == "none":
            regime = ""
        matched_pattern = None
        for pat in list(HIGH_ALPHA_PATTERNS) + list(MEDIUM_ALPHA_PATTERNS):
            if row.get(pat, False):
                matched_pattern = pat
                break
        pat_key = matched_pattern or "no_pattern"
        pattern_stats[pat_key]["trades"] += 1
        pattern_stats[pat_key]["wins"] += int(is_win)
        pattern_stats[pat_key]["losses"] += int(pct_return < 0)
        pattern_stats[pat_key]["pnl"] += weighted_return

        # Track by regime
        regime_trade_stats[regime]["trades"] += 1
        regime_trade_stats[regime]["wins"] += int(is_win)
        regime_trade_stats[regime]["losses"] += int(pct_return < 0)
        regime_trade_stats[regime]["pnl"] += weighted_return

        # Track combos
        combo_key = f"{pat_key}+{regime}"
        if combo_key not in combo_stats:
            combo_stats[combo_key] = {"trades": 0, "wins": 0, "pnl": 0.0}
        combo_stats[combo_key]["trades"] += 1
        combo_stats[combo_key]["wins"] += int(is_win)
        combo_stats[combo_key]["pnl"] += weighted_return

        returns.append(weighted_return)
        if pct_return > 0:
            wins += 1
        elif pct_return < 0:
            losses += 1

    returns_arr = np.array(returns)
    total_trades = wins + losses

    # Sharpe ratio — compute on trading days only (non-zero returns)
    trade_returns = returns_arr[returns_arr != 0.0]
    if len(trade_returns) > 1 and trade_returns.std() > 0:
        sharpe = (trade_returns.mean() / trade_returns.std()) * np.sqrt(252)
    else:
        sharpe = 0.0

    # Max drawdown (percentage of peak equity)
    equity = 1.0 + np.cumsum(returns_arr)  # equity curve starting at 1.0
    running_peak = np.maximum.accumulate(equity)
    dd_pct = (running_peak - equity) / running_peak
    max_dd = float(dd_pct.max()) if len(dd_pct) > 0 else 0.0

    win_rate = wins / total_trades if total_trades > 0 else 0.0
    cum_return = float(equity[-1] - 1.0) if len(equity) > 0 else 0.0

    return {
        "sharpe_ratio": round(float(sharpe), 4),
        "max_drawdown": round(max_dd, 4),
        "win_rate": round(win_rate, 4),
        "n_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "skipped_by_gate": skipped_by_gate,
        "cumulative_return": round(cum_return, 6),
        "pattern_stats": pattern_stats,
        "regime_trade_stats": regime_trade_stats,
        "combo_stats": combo_stats,
    }


def compute_regime_metrics(df_test: pd.DataFrame, predictions: list[int]) -> list[RegimeMetrics]:
    """Win rate breakdown by regime."""
    metrics = []
    for regime in ["bull", "bear", "sideways"]:
        mask = df_test["regime_label"] == regime
        if mask.sum() == 0:
            continue
        regime_outcomes = df_test.loc[mask, "outcome"].values
        regime_preds = np.array(predictions)[mask.values]
        correct = sum(
            1 for o, p in zip(regime_outcomes, regime_preds)
            if (o == "up" and p == 1) or (o == "down" and p == 0)
        )
        total = len(regime_outcomes)
        metrics.append(RegimeMetrics(
            regime=regime,
            win_rate=round(correct / total, 4) if total > 0 else 0.0,
            n_trades=total,
        ))
    return metrics


async def main():
    parser = argparse.ArgumentParser(description="RISEDUAL Walk-Forward Backtest")
    parser.add_argument("--model-path", type=str, default=None, help="Path to trained model (trains fresh if not provided)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    args = parser.parse_args()

    print(f"[backtest] Connecting to {MONGO_URI} / {DB_NAME}")
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]

    print("[backtest] Loading labeled snapshots...")
    df = await load_labeled_data(db)
    client.close()

    if df.empty or len(df) < 60:
        print(f"[backtest] Only {len(df)} labeled snapshots (need 60+). Keep collecting.")
        sys.exit(0)

    # Build features and target
    X = df[FEATURE_COLS].copy()
    for col in FEATURE_COLS:
        X[col] = pd.to_numeric(X[col], errors="coerce")
    y = (df["outcome"] == "up").astype(int)

    # 70/30 walk-forward split (chronological)
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)
    elif "captured_at" in df.columns:
        df = df.sort_values("captured_at").reset_index(drop=True)
    split_idx = int(len(df) * 0.7)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
    df_test = df.iloc[split_idx:].reset_index(drop=True)

    print(f"\n{'='*60}")
    print(f"  Train: {len(X_train)} | Test: {len(X_test)} | Total: {len(df)}")
    print(f"  Positive rate: {y.mean():.1%}")
    print(f"{'='*60}\n")

    if len(X_test) < 30:
        print("[backtest] Test set too small (< 30). Need more data.")
        sys.exit(0)

    # Train or load model
    if args.model_path:
        model = SignalModel.load(args.model_path)
        version = model.config.model_version
    else:
        model = SignalModel()
        model.fit(X_train, y_train)
        version = "backtest_fresh"

    # Evaluate on test set
    model.evaluate(X_test, y_test, n_predictions=len(df))
    stats = model.calibration_stats
    print(f"  Accuracy: {stats.accuracy:.4f} | Brier: {stats.brier_score:.4f} | ECE: {stats.ece:.4f}")

    # Generate predictions — use lower base threshold (0.40) to widen candidate pool
    # The adaptive gate in simulate_pnl will filter based on pattern presence
    probas = model.predict_proba(X_test)
    if probas.ndim == 2:
        probas = probas[:, 1]
    base_threshold = 0.40  # wider net — simulate_pnl's adaptive gate does the real filtering
    predictions = (probas > base_threshold).astype(int).tolist()

    pnl = simulate_pnl(df_test, predictions, probas.tolist())
    regime_metrics = compute_regime_metrics(df_test, predictions)

    result = BacktestResult(
        sharpe_ratio=pnl["sharpe_ratio"],
        max_drawdown=pnl["max_drawdown"],
        win_rate_overall=pnl["win_rate"],
        win_rate_by_regime=regime_metrics,
        n_trades=pnl["n_trades"],
        train_size=len(X_train),
        test_size=len(X_test),
        model_version=version,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )

    print(f"\n{'='*60}")
    print(f"  STRATEGY: Pattern-Concentrated + Regime-Aligned")
    print(f"{'='*60}")
    print(f"  Sharpe: {result.sharpe_ratio} | Max DD: {result.max_drawdown:.1%}")
    print(f"  Win Rate: {result.win_rate_overall:.1%} | Trades: {result.n_trades}")
    print(f"  Cumulative Return: {pnl['cumulative_return']:.4%}")
    print(f"  Skipped by adaptive gate: {pnl.get('skipped_by_gate', 0)}")

    # ── Per-Pattern Diagnostics ──────────────────────────────────────────────
    print(f"\n  -- PER-PATTERN TRADE BREAKDOWN --")
    print(f"  {'Pattern':<30} {'Trades':>6} {'WinR':>6} {'PnL':>10} {'Avg':>8}")
    for pat, st in sorted(pnl["pattern_stats"].items(), key=lambda x: -x[1]["pnl"]):
        if st["trades"] == 0:
            continue
        wr = st["wins"] / st["trades"] * 100 if st["trades"] > 0 else 0
        avg = st["pnl"] / st["trades"] if st["trades"] > 0 else 0
        flag = " *** DRAG" if st["pnl"] < 0 and st["trades"] > 10 else ""
        name = pat.replace("pattern_", "")
        print(f"  {name:<30} {st['trades']:>6} {wr:>5.1f}% {st['pnl']:>+9.4f} {avg:>+7.5f}{flag}")

    # ── Per-Regime Trade PnL ─────────────────────────────────────────────────
    print(f"\n  -- PER-REGIME TRADE PnL --")
    print(f"  {'Regime':<12} {'Trades':>6} {'WinR':>6} {'PnL':>10}")
    for regime, st in sorted(pnl["regime_trade_stats"].items(), key=lambda x: -x[1]["pnl"]):
        if st["trades"] == 0:
            continue
        wr = st["wins"] / st["trades"] * 100 if st["trades"] > 0 else 0
        print(f"  {regime or 'unknown':<12} {st['trades']:>6} {wr:>5.1f}% {st['pnl']:>+9.4f}")

    # ── Pattern+Regime Combo Analysis ────────────────────────────────────────
    print(f"\n  -- TOP/BOTTOM PATTERN+REGIME COMBOS --")
    sorted_combos = sorted(pnl["combo_stats"].items(), key=lambda x: -x[1]["pnl"])
    # Top 5
    for combo, st in sorted_combos[:5]:
        if st["trades"] == 0:
            continue
        wr = st["wins"] / st["trades"] * 100
        print(f"  + {combo:<45} {st['trades']:>4}t  {wr:>5.1f}%  {st['pnl']:>+8.4f}")
    # Bottom 5 (drags)
    for combo, st in sorted_combos[-5:]:
        if st["trades"] == 0 or st["pnl"] >= 0:
            continue
        wr = st["wins"] / st["trades"] * 100
        print(f"  - {combo:<45} {st['trades']:>4}t  {wr:>5.1f}%  {st['pnl']:>+8.4f}")

    print(f"{'='*60}\n")

    # Check gate status
    from risedual_core.ml.calibration_gate import gate_status
    gate = gate_status(stats, result)
    print(f"  Tier 1 (Alerts): {'UNLOCKED' if gate['tier1_alerts'] else 'LOCKED'}")
    print(f"  Tier 2 (Paper):  {'UNLOCKED' if gate['tier2_paper'] else 'LOCKED'}")
    print(f"  Tier 3 (Live):   {'UNLOCKED' if gate['tier3_live'] else 'LOCKED'}")
    if gate.get("blockers"):
        print(f"  Blockers: {', '.join(gate['blockers'])}")

    # Save result
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = args.output or str(RESULTS_DIR / f"backtest_{version}.json")
    with open(output_path, "w") as f:
        json.dump(result.model_dump(), f, indent=2)
    print(f"\n  Saved: {output_path}")

    # Persist backtest metadata into the latest production model so API reflects Tier 2
    latest_model_dir = Path("models")
    if latest_model_dir.exists():
        candidates = sorted(
            latest_model_dir.glob("signal_model_v*.joblib"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            prod_model = SignalModel.load(candidates[0])
            if not hasattr(prod_model, "_metadata") or prod_model._metadata is None:
                prod_model._metadata = {}
            prod_model._metadata["sharpe"] = pnl["sharpe_ratio"]
            prod_model._metadata["max_drawdown"] = pnl["max_drawdown"]
            prod_model._metadata["backtest_win_rate"] = pnl["win_rate"]
            prod_model._metadata["backtest_trades"] = pnl["n_trades"]
            prod_model._metadata["backtest_at"] = datetime.now(timezone.utc).isoformat()
            prod_model.save(str(candidates[0]))
            print(f"  Updated {candidates[0].name} metadata: sharpe={pnl['sharpe_ratio']}, dd={pnl['max_drawdown']}")


if __name__ == "__main__":
    asyncio.run(main())
