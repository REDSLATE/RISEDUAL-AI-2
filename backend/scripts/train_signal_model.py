#!/usr/bin/env python3
"""Train Signal Model — standalone script.

Reads labeled FeaturesSnapshots from MongoDB, trains XGBoost + Platt calibration,
evaluates on 80/20 split, and saves the model to /app/backend/models/.

Usage:
    cd /app/backend && python scripts/train_signal_model.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from risedual_core.ml.calibration import brier_score, expected_calibration_error
from risedual_core.ml.signal_model import SignalModel
from risedual_core.schemas.market import FeaturesSnapshot

MONGO_URI: str = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME: str = os.environ.get("DB_NAME", "risedual_db")
COLLECTION: str = "features_snapshots"
MODELS_DIR: Path = Path("/app/backend/models")
MIN_SAMPLES: int = 100

FEATURE_COLS: list[str] = [
    "rsi_14", "macd", "macd_signal", "sma_20", "sma_50",
    "volume_ratio", "sentiment_score", "insider_activity", "sector_momentum",
]


async def load_labeled_data(db: AsyncIOMotorDatabase) -> pd.DataFrame:
    cursor = db[COLLECTION].find(
        {"outcome": {"$nin": [None, "error"]}},
        projection={"_id": 0, "ticker": 1, "outcome": 1, "captured_at": 1, "regime_label": 1,
                     **{col: 1 for col in FEATURE_COLS}},
    )
    docs: list[dict[str, Any]] = await cursor.to_list(length=None)
    if not docs:
        return pd.DataFrame()
    return pd.DataFrame(docs)


def build_Xy(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    X = df[FEATURE_COLS].copy()
    for col in FEATURE_COLS:
        X[col] = pd.to_numeric(X[col], errors="coerce")
    if "predicted_direction" in df.columns:
        y = (df["outcome"] == df["predicted_direction"]).astype(int)
    else:
        y = (df["outcome"] == "up").astype(int)
    return X, y


def next_model_version(models_dir: Path) -> int:
    existing = list(models_dir.glob("signal_model_v*.joblib"))
    if not existing:
        return 1
    versions = []
    for p in existing:
        try:
            versions.append(int(p.stem.split("_v")[-1]))
        except ValueError:
            pass
    return max(versions, default=0) + 1


async def main() -> None:
    print(f"[train_signal_model] Connecting to {MONGO_URI} / {DB_NAME}")
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]

    print("[train_signal_model] Loading labeled snapshots...")
    df = await load_labeled_data(db)
    client.close()

    if df.empty:
        print("[train_signal_model] No labeled data found. Exiting.")
        sys.exit(0)

    X, y = build_Xy(df)
    n = len(df)
    pos = int(y.sum())
    print(f"\n{'='*60}")
    print(f"  Total: {n} | Positive: {pos} ({100*pos/n:.1f}%) | Tickers: {df['ticker'].nunique()}")
    print(f"{'='*60}\n")

    if len(X) < MIN_SAMPLES:
        print(f"[train_signal_model] Only {len(X)} samples (need {MIN_SAMPLES}). Keep collecting.")
        sys.exit(0)

    print("[train_signal_model] Training SignalModel (XGBoost + Platt calibration)...")
    model = SignalModel()
    model.fit(X, y)

    # Evaluate on 80/20 split
    from sklearn.metrics import accuracy_score
    from sklearn.model_selection import train_test_split

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    model_eval = SignalModel()
    model_eval.fit(X_train, y_train)

    X_test_filled = X_test.fillna(X_test.median())
    raw_probs, predictions = [], []
    for _, row in X_test_filled.iterrows():
        snapshot = FeaturesSnapshot(
            ticker="EVAL", timestamp=datetime.now(timezone.utc),
            **{col: (float(row[col]) if pd.notna(row[col]) else None) for col in FEATURE_COLS},
        )
        result = model_eval.predict(snapshot)
        raw_probs.append(result.raw_probability)
        predictions.append(1 if result.direction.value == "up" else 0)

    acc = accuracy_score(y_test.tolist(), predictions)
    bs = brier_score(y_test.tolist(), raw_probs)
    ece = expected_calibration_error(y_test.tolist(), raw_probs)

    print(f"\n{'='*60}")
    print(f"  Accuracy: {acc:.4f} | Brier: {bs:.4f} | ECE: {ece:.4f}")
    print(f"{'='*60}\n")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    version = next_model_version(MODELS_DIR)
    model_path = MODELS_DIR / f"signal_model_v{version}.joblib"
    model.save(str(model_path))
    print(f"[train_signal_model] Saved {model_path} (v{version}, {len(X)} samples)")


if __name__ == "__main__":
    asyncio.run(main())
