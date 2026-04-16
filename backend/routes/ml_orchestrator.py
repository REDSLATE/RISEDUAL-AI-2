"""ML status endpoints — Phase 3 observability.

Routes
------
GET /api/ml/gate-status
    Returns tier unlock status, thresholds, and next milestone.

GET /api/ml/stats
    Returns data progress, pattern detection counts, label counts, and
    the model's CalibrationStats if a trained model is available.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from motor.motor_asyncio import AsyncIOMotorDatabase

from risedual_core.ml.calibration import (
    GateResult,
    Tier,
    check_all_gates,
    _T1_MIN_ACCURACY,
    _T1_MIN_PREDICTIONS,
    _T1_MAX_ECE,
    _T2_MIN_ACCURACY,
    _T2_MIN_SHARPE,
    _T2_MAX_DRAWDOWN,
    _T3_MIN_ACCURACY,
    _T3_MIN_SHARPE,
    _T3_MAX_DRAWDOWN,
    _T3_MIN_LIVE_DAYS,
)
from risedual_core.ml.features import PATTERN_COLUMNS
from risedual_core.ml.signal_model import SignalModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ml", tags=["ml"])

db: AsyncIOMotorDatabase | None = None
_MODELS_DIR: Path = Path(os.getenv("MODELS_DIR", "models"))


def set_db(database: AsyncIOMotorDatabase) -> None:
    global db
    db = database


# ── Model loader ──────────────────────────────────────────────────────────────

def _latest_model() -> SignalModel | None:
    """Load the most recently modified signal model from disk."""
    if not _MODELS_DIR.exists():
        return None
    candidates = sorted(
        _MODELS_DIR.glob("signal_model_v*.joblib"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    try:
        return SignalModel.load(candidates[0])
    except Exception as exc:
        log.warning("[ml_api] Model load failed: %s", exc)
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gate_thresholds() -> dict[str, Any]:
    return {
        "tier1": {
            "min_accuracy": _T1_MIN_ACCURACY,
            "min_predictions": _T1_MIN_PREDICTIONS,
            "max_ece": _T1_MAX_ECE,
        },
        "tier2": {
            "min_accuracy": _T2_MIN_ACCURACY,
            "min_sharpe": _T2_MIN_SHARPE,
            "max_drawdown": _T2_MAX_DRAWDOWN,
        },
        "tier3": {
            "min_accuracy": _T3_MIN_ACCURACY,
            "min_sharpe": _T3_MIN_SHARPE,
            "max_drawdown": _T3_MAX_DRAWDOWN,
            "min_live_days": _T3_MIN_LIVE_DAYS,
        },
    }


def _next_milestone(gate: GateResult, n_predictions: int) -> dict[str, Any]:
    """Return the single next action the user should take."""
    if not gate.tier1.unlocked:
        remaining = max(0, _T1_MIN_PREDICTIONS - n_predictions)
        return {
            "milestone": "Unlock Tier 1 Alerts",
            "description": (
                f"Collect {remaining} more labeled snapshots, "
                "then run train_signal_model.py"
            ),
            "predictions_needed": remaining,
        }
    if not gate.tier2.unlocked:
        return {
            "milestone": "Unlock Tier 2 Paper Trading",
            "description": (
                "Run scripts/backtest.py — Sharpe >= 1.0 and DD < 15% required"
            ),
            "predictions_needed": 0,
        }
    if not gate.tier3.unlocked:
        return {
            "milestone": "Unlock Tier 3 Live Execution",
            "description": (
                "Achieve 30 days of paper trading with Sharpe >= 1.2, "
                "DD < 12%, and set RISEDUAL_LIVE_EXECUTION=1"
            ),
            "predictions_needed": 0,
        }
    return {
        "milestone": "All tiers unlocked",
        "description": "Live execution is active",
        "predictions_needed": 0,
    }


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/gate-status")
async def get_gate_status() -> dict[str, Any]:
    """Return current tier unlock status and thresholds.

    No database queries — reads from the trained model artefact only.
    Returns locked status with zero-filled stats if no model exists yet.
    """
    model = _latest_model()
    stats = model.calibration_stats if model else None

    accuracy = stats.accuracy if stats else 0.0
    n_predictions = stats.n_predictions if stats else 0
    ece = stats.ece if stats else 1.0
    live_days = int(os.getenv("RISEDUAL_LIVE_DAYS", "0"))
    user_opted_in = os.getenv("RISEDUAL_LIVE_EXECUTION", "0") == "1"

    meta: dict[str, Any] = getattr(model, "_metadata", {}) or {} if model else {}
    sharpe = float(meta.get("sharpe", 0.0))
    max_drawdown = float(meta.get("max_drawdown", 1.0))

    gate = check_all_gates(
        accuracy=accuracy,
        n_predictions=n_predictions,
        ece=ece,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        live_days=live_days,
        user_opted_in=user_opted_in,
    )

    return {
        "highest_tier": gate.highest_unlocked.value,
        "tiers": {
            "tier1_alerts": {
                "unlocked": gate.tier1.unlocked,
                "reason": gate.tier1.reason,
            },
            "tier2_paper": {
                "unlocked": gate.tier2.unlocked,
                "reason": gate.tier2.reason,
            },
            "tier3_live": {
                "unlocked": gate.tier3.unlocked,
                "reason": gate.tier3.reason,
            },
        },
        "current_stats": {
            "accuracy": round(accuracy, 4),
            "n_predictions": n_predictions,
            "ece": round(ece, 4),
            "sharpe": round(sharpe, 4),
            "max_drawdown": round(max_drawdown, 4),
            "live_days": live_days,
        },
        "thresholds": _gate_thresholds(),
        "next_milestone": _next_milestone(gate, n_predictions),
        "model_available": model is not None,
    }


@router.get("/stats")
async def get_ml_stats() -> dict[str, Any]:
    """Return data progress, pattern counts, and calibration stats."""
    if db is None:
        return {"error": "Database not available"}

    snapshots_coll = db["features_snapshots"]

    # ── Label counts ─────────────────────────────────────────────────────────
    total_snapshots: int = await snapshots_coll.count_documents({})
    labeled: int = await snapshots_coll.count_documents(
        {"outcome": {"$nin": [None, "error", "pending"]}}
    )
    pending: int = await snapshots_coll.count_documents({"outcome": None})
    schema_v2: int = await snapshots_coll.count_documents({"schema_version": 2})

    # ── Outcome distribution ─────────────────────────────────────────────────
    outcome_pipeline = [
        {"$match": {"outcome": {"$nin": [None, "error", "pending"]}}},
        {"$group": {"_id": "$outcome", "count": {"$sum": 1}}},
    ]
    outcome_dist: dict[str, int] = {}
    async for doc in snapshots_coll.aggregate(outcome_pipeline):
        outcome_dist[doc["_id"]] = doc["count"]

    # ── Pattern detection counts (Phase 2 snapshots only) ────────────────────
    pattern_counts: dict[str, int] = {}
    for col in PATTERN_COLUMNS:
        count = await snapshots_coll.count_documents(
            {"schema_version": 2, col: True}
        )
        pattern_counts[col.replace("pattern_", "")] = count

    # ── Paper trades ─────────────────────────────────────────────────────────
    paper_open: int = 0
    paper_total: int = 0
    paper_pnl: float = 0.0
    try:
        paper_open = await db["paper_trades"].count_documents({"status": "open"})
        paper_total = await db["paper_trades"].count_documents({})
        pnl_pipeline = [
            {"$match": {"pnl_usd": {"$ne": None}}},
            {"$group": {"_id": None, "total_pnl": {"$sum": "$pnl_usd"}}},
        ]
        async for doc in db["paper_trades"].aggregate(pnl_pipeline):
            paper_pnl = round(float(doc.get("total_pnl", 0.0)), 2)
    except Exception:
        pass

    # ── Live orders ───────────────────────────────────────────────────────────
    live_orders: int = 0
    try:
        live_orders = await db["live_orders"].count_documents({})
    except Exception:
        pass

    # ── CalibrationStats from model ───────────────────────────────────────────
    model = _latest_model()
    stats = model.calibration_stats if model else None
    calibration: dict[str, Any] = {}
    if stats:
        calibration = {
            "accuracy": round(stats.accuracy, 4),
            "brier_score": round(stats.brier_score, 4),
            "ece": round(stats.ece, 4),
            "n_predictions": stats.n_predictions,
            "model_version": stats.model_version,
            "evaluated_at": stats.evaluated_at.isoformat() if stats.evaluated_at else None,
        }

    # ── Phase 3 progress milestones ───────────────────────────────────────────
    milestones = {
        "first_train_ready": labeled >= 100,
        "first_backtest_ready": labeled >= 200,
        "tier1_prediction_count": labeled >= _T1_MIN_PREDICTIONS,
    }

    return {
        "data_progress": {
            "total_snapshots": total_snapshots,
            "labeled": labeled,
            "pending_labels": pending,
            "schema_v2_snapshots": schema_v2,
            "outcome_distribution": outcome_dist,
        },
        "pattern_detection_counts": pattern_counts,
        "paper_trading": {
            "open_trades": paper_open,
            "total_trades": paper_total,
            "total_pnl_usd": paper_pnl,
        },
        "live_execution": {
            "total_orders": live_orders,
        },
        "calibration": calibration,
        "milestones": milestones,
    }
