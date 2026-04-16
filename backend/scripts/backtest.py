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
    "pattern_double_bottom", "pattern_bullish_engulfing", "pattern_bearish_engulfing",
    "pattern_bull_flag", "pattern_rsi_divergence", "pattern_macd_crossover",
    "pattern_volume_surge", "pattern_head_and_shoulders",
]


async def load_labeled_data(db) -> pd.DataFrame:
    cursor = db[COLLECTION].find(
        {"outcome": {"$in": ["up", "down", "flat"]}},
        projection={"_id": 0, "ticker": 1, "outcome": 1, "captured_at": 1,
                     "regime_label": 1, "prediction_price": 1, "outcome_price": 1,
                     **{col: 1 for col in FEATURE_COLS}},
    )
    docs = await cursor.to_list(length=None)
    if not docs:
        return pd.DataFrame()
    return pd.DataFrame(docs)


def simulate_pnl(df_test: pd.DataFrame, predictions: list[int], probabilities: list[float]) -> dict:
    """Simulate simple long-only P&L from model predictions."""
    returns = []
    wins = 0
    losses = 0

    for i, (_, row) in enumerate(df_test.iterrows()):
        pred = predictions[i]
        conf = probabilities[i]

        if pred == 0:  # model says skip (bearish)
            returns.append(0.0)
            continue

        # Calculate actual return
        pred_price = row.get("prediction_price")
        out_price = row.get("outcome_price")
        if pred_price and out_price and pred_price > 0:
            pct_return = (out_price - pred_price) / pred_price
        else:
            outcome = row.get("outcome", "flat")
            pct_return = 0.015 if outcome == "up" else (-0.015 if outcome == "down" else 0.0)

        # Position size based on confidence (simplified Kelly)
        position_size = min(0.25, max(0.05, (conf - 0.5) * 2))
        weighted_return = pct_return * position_size

        returns.append(weighted_return)
        if pct_return > 0:
            wins += 1
        elif pct_return < 0:
            losses += 1

    returns_arr = np.array(returns)
    total_trades = wins + losses

    # Sharpe ratio (annualized, assuming daily)
    if len(returns_arr) > 1 and returns_arr.std() > 0:
        sharpe = (returns_arr.mean() / returns_arr.std()) * np.sqrt(252)
    else:
        sharpe = 0.0

    # Max drawdown
    cumulative = np.cumsum(returns_arr)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = running_max - cumulative
    max_dd = float(drawdowns.max()) if len(drawdowns) > 0 else 0.0

    win_rate = wins / total_trades if total_trades > 0 else 0.0

    return {
        "sharpe_ratio": round(float(sharpe), 4),
        "max_drawdown": round(max_dd, 4),
        "win_rate": round(win_rate, 4),
        "n_trades": total_trades,
        "wins": wins,
        "losses": losses,
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

    # Generate predictions for P&L simulation
    probas = model.predict_proba(X_test)
    predictions = (probas > 0.5).astype(int).tolist()

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
    print(f"  Sharpe: {result.sharpe_ratio} | Max DD: {result.max_drawdown:.1%}")
    print(f"  Win Rate: {result.win_rate_overall:.1%} | Trades: {result.n_trades}")
    for rm in regime_metrics:
        print(f"    {rm.regime}: {rm.win_rate:.1%} ({rm.n_trades} trades)")
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


if __name__ == "__main__":
    asyncio.run(main())
