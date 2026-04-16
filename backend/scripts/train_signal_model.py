"""Signal model training script — supports live snapshots and historical backfill.

Connects to MongoDB, loads labeled ``features_snapshots`` documents
(schema_version 1, 2, or 3), trains a :class:`~risedual_core.ml.signal_model.SignalModel`,
evaluates calibration, and saves the artefact to ``models/``.

Key improvements over v1
------------------------
- Loads schema_version=3 backfill rows (``outcome_1d`` / ``outcome_5d`` fields)
- Supports ``--target`` flag: ``outcome_1d`` (default), ``outcome_5d``, or ``direction``
- Handles mixed schema versions in a single training run
- Richer evaluation report: per-regime accuracy, per-pattern lift, class balance
- Auto-selects best available feature columns for the loaded dataset
- Chronological train/eval split (no look-ahead leakage)
- Prints calibration gate readiness with specific blockers

Usage
-----
    python scripts/train_signal_model.py
    python scripts/train_signal_model.py --target outcome_5d
    python scripts/train_signal_model.py --min-samples 1000 --eval-frac 0.15

Environment variables
---------------------
    MONGO_URI       MongoDB connection string (default: mongodb://localhost:27017)
    DB_NAME         Database name            (default: risedual)
    MODELS_DIR      Artefact output path     (default: ./models)
    MIN_SAMPLES     Minimum labeled rows     (default: 100)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Configuration ─────────────────────────────────────────────────────────────

# Load .env for MONGO_URL, DB_NAME etc.
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

MONGO_URI: str   = os.environ.get("MONGO_URL", os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
DB_NAME: str     = os.environ.get("DB_NAME",     "risedual_db")
COLLECTION: str  = "features_snapshots"
MODELS_DIR: Path = Path(os.environ.get("MODELS_DIR", "models"))
MIN_SAMPLES: int = int(os.environ.get("MIN_SAMPLES", "100"))
EVAL_SPLIT: float = 0.20

from risedual_core.ml.features import FEATURE_COLUMNS, PATTERN_COLUMNS
from risedual_core.ml.signal_model import CalibrationStats, SignalModel, SignalModelConfig
from risedual_core.schemas.market import PredictionDirection


# ── Data loading ──────────────────────────────────────────────────────────────


async def load_labeled_data(db: Any, target_field: str) -> "pd.DataFrame":  # noqa: F821
    """Load all labeled snapshots from MongoDB — all schema versions.

    Priority for target resolution:
    - schema_version=3 (backfill): use ``outcome_1d`` or ``outcome_5d`` directly
    - schema_version=1/2 (live):   use ``outcome`` field

    The ``target_field`` arg controls which outcome column to use when both
    are available.

    Returns an empty DataFrame if nothing qualifies.
    """
    import pandas as pd

    # Query: doc must have at least one valid outcome field
    cursor = db[COLLECTION].find(
        {
            "$or": [
                {"outcome": {"$nin": [None, "error", "pending"]}},
                {"outcome_1d": {"$nin": [None, "error", "pending"]}},
                {"outcome_5d": {"$nin": [None, "error", "pending"]}},
            ]
        },
        {
            "_id": 0,
            "ticker": 1,
            "timestamp": 1,
            "captured_at": 1,
            "outcome": 1,
            "outcome_1d": 1,
            "outcome_5d": 1,
            "predicted_direction": 1,
            "regime_label": 1,
            "schema_version": 1,
            "source": 1,
            **{col: 1 for col in FEATURE_COLUMNS},
        },
    )
    docs = await cursor.to_list(length=None)
    if not docs:
        return pd.DataFrame()

    df = pd.DataFrame(docs)

    # Resolve unified timestamp column
    if "timestamp" not in df.columns and "captured_at" in df.columns:
        df["timestamp"] = df["captured_at"]
    elif "timestamp" not in df.columns:
        df["timestamp"] = None

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Resolve unified outcome column for training
    # For backfill rows (schema_version=3), prefer the requested target_field
    def _resolve_outcome(row: "pd.Series") -> str | None:  # noqa: F821
        sv = row.get("schema_version", 1)
        if sv == 3:
            val = row.get(target_field)
            if val and val not in ("error", "pending"):
                return val
        # Fall back to live outcome field
        val = row.get("outcome")
        if val and val not in ("error", "pending"):
            return val
        # Last resort: try 1d
        val = row.get("outcome_1d")
        return val if val and val not in ("error", "pending") else None

    df["_outcome"] = df.apply(_resolve_outcome, axis=1)
    df = df[df["_outcome"].notna()].copy()

    sv_counts = df.get("schema_version", 0).value_counts().to_dict()
    sources = df.get("source", "live").value_counts().to_dict()
    print(
        f"[train] Loaded {len(df):,} labeled rows — "
        f"schema versions: {sv_counts} | sources: {sources}"
    )
    return df


# ── Feature / label construction ──────────────────────────────────────────────


def build_Xy(df: "pd.DataFrame") -> tuple["pd.DataFrame", "pd.Series"]:  # noqa: N802
    """Build feature matrix X and binary target y.

    Target: ``is_correct = 1`` when outcome matches predicted_direction, or
    when outcome == "up" (positive class) for backfill rows without a
    predicted_direction.
    """
    import pandas as pd

    # Binary target: correct prediction or up-move for backfill
    if "predicted_direction" in df.columns and df["predicted_direction"].notna().any():
        is_correct = (df["_outcome"] == df["predicted_direction"]).astype(int)
    else:
        is_correct = (df["_outcome"] == "up").astype(int)

    df = df.copy()

    # Regime encoding
    if "regime_label" in df.columns:
        regime_map = {"bull": 1, "bear": -1, "sideways": 0}
        df["regime_encoded"] = df["regime_label"].map(regime_map).fillna(0).astype(float)

    # Build feature list from what's actually present
    available = [c for c in FEATURE_COLUMNS if c in df.columns]
    if "regime_encoded" in df.columns and "regime_encoded" not in available:
        available.append("regime_encoded")

    X = df[available].copy()
    return X, is_correct


# ── Stats ─────────────────────────────────────────────────────────────────────


def print_dataset_stats(
    df: "pd.DataFrame",
    X: "pd.DataFrame",
    y: "pd.Series",
    target_field: str,
) -> None:
    """Print a comprehensive dataset summary before training."""
    print()
    print("=" * 65)
    print("  DATASET STATISTICS")
    print("=" * 65)
    print(f"  Total labeled rows    : {len(df):,}")
    print(f"  Target field          : {target_field}")
    print(f"  Positive class (up)   : {y.mean()*100:.1f}%  ({y.sum():,} rows)")
    print(f"  Negative class        : {(1-y).mean()*100:.1f}%  ({(1-y).sum():,} rows)")
    print(f"  Feature columns       : {len(X.columns)}")
    print(f"    Numeric (indicators): {len([c for c in X.columns if c not in PATTERN_COLUMNS])}")
    print(f"    Pattern booleans    : {len([c for c in X.columns if c in PATTERN_COLUMNS])}")

    # Date range
    if "timestamp" in df.columns:
        dates = df["timestamp"].dropna()
        if not dates.empty:
            print(f"  Date range            : {dates.min().date()} → {dates.max().date()}")

    # Schema version breakdown
    if "schema_version" in df.columns:
        sv_counts = df["schema_version"].value_counts().sort_index()
        print(f"  Schema version split  : {sv_counts.to_dict()}")

    # Source breakdown (live vs yfinance)
    if "source" in df.columns:
        src_counts = df["source"].fillna("live").value_counts()
        print(f"  Data sources          : {src_counts.to_dict()}")

    # Ticker coverage
    if "ticker" in df.columns:
        n_tickers = df["ticker"].nunique()
        top_tickers = df["ticker"].value_counts().head(5).to_dict()
        print(f"  Tickers               : {n_tickers} unique")
        print(f"  Top 5 by row count    : {top_tickers}")

    # Per-regime accuracy
    if "regime_label" in df.columns and "regime_encoded" in X.columns:
        print()
        print("  Accuracy by regime:")
        for regime in ["bull", "bear", "sideways"]:
            mask = df["regime_label"] == regime
            if mask.sum() > 0:
                acc = y[mask].mean()
                print(f"    {regime:<12} n={mask.sum():>6,}  acc={acc:.1%}")

    # Pattern detection rates + accuracy lift
    pattern_cols_present = [c for c in PATTERN_COLUMNS if c in df.columns]
    if pattern_cols_present:
        print()
        print("  Pattern detection rates + accuracy lift:")
        for col in pattern_cols_present:
            name = col.replace("pattern_", "")
            pat_mask = df[col].fillna(False).astype(bool)
            rate = pat_mask.mean()
            with_acc = y[pat_mask].mean() if pat_mask.sum() > 0 else float("nan")
            without_acc = y[~pat_mask].mean() if (~pat_mask).sum() > 0 else float("nan")
            lift = with_acc - without_acc if not (
                isinstance(with_acc, float) and with_acc != with_acc
            ) else 0.0
            lift_str = f"{lift:+.1%}" if lift == lift else "  n/a"
            print(
                f"    {name:<28} {rate*100:4.1f}%  "
                f"with={with_acc:.1%}  without={without_acc:.1%}  lift={lift_str}"
            )

    # High-null feature warning
    numeric_cols = [c for c in X.columns if c not in PATTERN_COLUMNS]
    high_null = [(c, X[c].isna().mean()) for c in numeric_cols if X[c].isna().mean() > 0.1]
    if high_null:
        print()
        print("  Features with >10% nulls (will be imputed):")
        for col, rate in high_null:
            flag = " *** HIGH ***" if rate > 0.5 else ""
            print(f"    {col:<38} {rate*100:5.1f}%{flag}")

    print("=" * 65)
    print()


# ── Evaluation report ─────────────────────────────────────────────────────────


def print_eval_report(
    stats: CalibrationStats,
    X_eval: "pd.DataFrame",
    y_eval: "pd.Series",
    df_eval: "pd.DataFrame",
    model: SignalModel,
) -> None:
    """Print a full evaluation report after training."""
    import numpy as np

    print()
    print("=" * 65)
    print("  EVALUATION RESULTS")
    print("=" * 65)
    print(f"  Accuracy    : {stats.accuracy*100:.2f}%")
    print(f"  Brier score : {stats.brier_score:.4f}  (lower = better calibrated)")
    print(f"  ECE         : {stats.ece:.4f}          (lower = better calibrated)")
    print(f"  Eval rows   : {len(X_eval):,}")
    print(f"  Total labels: {stats.n_predictions:,}")

    # Per-regime accuracy on eval set
    if "regime_label" in df_eval.columns:
        print()
        print("  Eval set accuracy by regime:")
        proba = model.predict_proba(X_eval)
        if proba.ndim == 2:
            preds = (proba[:, 1] >= 0.5).astype(int)
        else:
            preds = (proba >= 0.5).astype(int)
        for regime in ["bull", "bear", "sideways"]:
            mask = (df_eval["regime_label"] == regime).values
            if mask.sum() > 0:
                acc = (preds[mask] == y_eval.values[mask]).mean()
                print(f"    {regime:<12} n={mask.sum():>5,}  acc={acc:.1%}")

    # Feature importance from underlying XGBoost model
    print()
    print("  Feature importance (XGBoost):")
    try:
        import joblib
        model_path = sorted(
            Path("models").glob("signal_model_v*.joblib"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if model_path:
            artefact = joblib.load(model_path[0])

            # Method 1: Use pre-computed importances from artefact
            importances = artefact.get("feature_importances", {})

            # Method 2: Extract from CalibratedClassifierCV → XGBClassifier
            if not importances:
                underlying = artefact["model"]
                base = None
                if hasattr(underlying, "calibrated_classifiers_"):
                    base = underlying.calibrated_classifiers_[0].estimator
                elif hasattr(underlying, "estimator"):
                    base = underlying.estimator
                if base and hasattr(base, "feature_importances_"):
                    importances = dict(zip(X_eval.columns, base.feature_importances_))

            if importances:
                for feat, imp in sorted(importances.items(), key=lambda x: -x[1]):
                    bar = "=" * int(imp * 80)
                    print(f"    {feat:<38} {imp:.4f}  {bar}")
            else:
                print("    (no feature importances available)")
    except Exception as exc:
        print(f"    (feature importance extraction failed: {exc})")

    # Phase 3 gate readiness
    print()
    print("  Phase 3 calibration gate:")
    t1_ok = stats.accuracy >= 0.55 and stats.ece < 0.15 and stats.n_predictions >= 100
    if t1_ok:
        print("  ✅  Tier 1 (alerts)       READY")
    else:
        reasons = []
        if stats.accuracy < 0.55:        reasons.append(f"accuracy {stats.accuracy*100:.1f}% < 55%")
        if stats.ece >= 0.15:            reasons.append(f"ECE {stats.ece:.4f} >= 0.15")
        if stats.n_predictions < 100:    reasons.append(f"n={stats.n_predictions} < 100")
        print(f"  ⏳  Tier 1 (alerts)       NOT YET — {'; '.join(reasons)}")

    t2_ok = stats.accuracy >= 0.60 and stats.n_predictions >= 500
    if t2_ok:
        print("  ✅  Tier 2 (paper trade)  accuracy/data gate passed — run backtest to confirm Sharpe/DD")
    else:
        reasons = []
        if stats.accuracy < 0.60:        reasons.append(f"accuracy {stats.accuracy*100:.1f}% < 60%")
        if stats.n_predictions < 500:    reasons.append(f"n={stats.n_predictions:,} < 500")
        print(f"  ⏳  Tier 2 (paper trade)  NOT YET — {'; '.join(reasons)}")

    print("=" * 65)


# ── Version management ────────────────────────────────────────────────────────


def next_model_version(models_dir: Path) -> int:
    existing = list(models_dir.glob("signal_model_v*.joblib"))
    if not existing:
        return 1
    versions: list[int] = []
    for p in existing:
        try:
            versions.append(int(p.stem.split("_v")[-1]))
        except ValueError:
            pass
    return max(versions, default=0) + 1


# ── Main ──────────────────────────────────────────────────────────────────────


async def main(target_field: str = "outcome_1d", min_samples: int = MIN_SAMPLES, eval_frac: float = EVAL_SPLIT) -> None:
    """Load data, train, evaluate, save."""
    try:
        import pandas as pd
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError as exc:
        print(f"[train] Missing dependency: {exc}. pip install pandas motor", file=sys.stderr)
        sys.exit(1)

    print(f"[train] Connecting to {MONGO_URI}/{DB_NAME} | target={target_field}")
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]

    df = await load_labeled_data(db, target_field)
    client.close()

    if df.empty:
        print("[train] No labeled data found. Run the backfill or collect live snapshots first.")
        sys.exit(0)

    X, y = build_Xy(df)
    print_dataset_stats(df, X, y, target_field)

    if len(X) < min_samples:
        print(
            f"[train] {len(X):,} samples — need {min_samples:,}. "
            "Run backfill or collect more live labels."
        )
        sys.exit(0)

    # Chronological split
    n_eval = max(1, int(len(X) * eval_frac))
    X_train, X_eval = X.iloc[:-n_eval], X.iloc[-n_eval:]
    y_train, y_eval = y.iloc[:-n_eval], y.iloc[-n_eval:]
    df_eval = df.iloc[-n_eval:].copy()

    print(f"[train] Train: {len(X_train):,} rows  |  Eval: {len(X_eval):,} rows")

    version = next_model_version(MODELS_DIR)
    config = SignalModelConfig(
        model_version=f"0.{version}.0",
        feature_columns=list(X.columns),
    )
    model = SignalModel(config=config)

    print(f"[train] Fitting SignalModel v{config.model_version}…")
    model.fit(X_train, y_train)

    stats: CalibrationStats = model.evaluate(X_eval, y_eval, n_predictions=len(df))

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    save_path = MODELS_DIR / f"signal_model_v{version}.joblib"
    model.save(save_path)

    print_eval_report(stats, X_eval, y_eval, df_eval, model)

    print(f"\n[train] Model saved → {save_path}")
    print(f"[train] Done at {stats.evaluated_at.isoformat()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train RISEDUAL signal model")
    parser.add_argument(
        "--target",
        choices=["outcome_1d", "outcome_5d", "direction"],
        default="outcome_1d",
        help="Which outcome field to use as the training target (default: outcome_1d).",
    )
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    parser.add_argument("--eval-frac", type=float, default=EVAL_SPLIT)
    args = parser.parse_args()
    asyncio.run(main(
        target_field=args.target,
        min_samples=args.min_samples,
        eval_frac=args.eval_frac,
    ))
