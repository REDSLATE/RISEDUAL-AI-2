"""ML Autonomous Action Orchestrator — ties the signal model to all 3 tiers.

Called from the signal endpoint after a prediction. Checks each tier's gate
and dispatches the appropriate action (alert, paper trade, or live trade).
Also provides a status endpoint showing current gate readiness.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from risedual_core.ml.calibration_gate import gate_status
from risedual_core.schemas.market import BacktestResult, CalibrationStats, SignalResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ml", tags=["ml"])
db = None

BACKTEST_DIR = Path("/app/backend/backtest_results")


def set_db(database):
    global db
    db = database


def _load_latest_backtest() -> BacktestResult | None:
    """Load the most recent backtest result from disk."""
    if not BACKTEST_DIR.exists():
        return None
    files = list(BACKTEST_DIR.glob("backtest_*.json"))
    if not files:
        return None
    latest = max(files, key=lambda f: f.stat().st_mtime)
    try:
        with open(latest) as f:
            data = json.load(f)
        return BacktestResult(**data)
    except Exception:
        return None


def _load_model_stats() -> CalibrationStats | None:
    """Load calibration stats from the latest trained model."""
    models_dir = Path(os.environ.get("MODELS_DIR", "/app/backend/models"))
    candidates = list(models_dir.glob("signal_model_v*.joblib"))
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        from risedual_core.ml.signal_model import SignalModel
        model = SignalModel.load(str(latest))
        return model.calibration_stats
    except Exception:
        return None


async def _get_days_live(database: AsyncIOMotorDatabase) -> int:
    """Count days since first ML alert was sent."""
    first_alert = await database["ml_alerts"].find_one(
        {}, sort=[("created_at", 1)], projection={"created_at": 1}
    )
    if not first_alert or "created_at" not in first_alert:
        return 0
    first_date = first_alert["created_at"]
    if isinstance(first_date, str):
        first_date = datetime.fromisoformat(first_date)
    delta = datetime.now(timezone.utc) - first_date.replace(tzinfo=timezone.utc)
    return max(0, delta.days)


@router.get("/gate-status", summary="Get ML tier gate readiness")
async def get_gate_status():
    """Returns current calibration gate status for all 3 tiers."""
    stats = _load_model_stats()
    backtest = _load_latest_backtest()
    days_live = await _get_days_live(db) if db is not None else 0

    return gate_status(stats, backtest, days_live)


@router.get("/stats", summary="Get ML pipeline statistics")
async def get_ml_stats():
    """Returns data collection stats and model readiness."""
    if db is None:
        return {"error": "Database not available"}

    total_snapshots = await db["features_snapshots"].count_documents({})
    labeled = await db["features_snapshots"].count_documents({"outcome": {"$ne": None}})
    unlabeled = await db["features_snapshots"].count_documents({"outcome": None})
    errors = await db["features_snapshots"].count_documents({"outcome": "error"})

    # Pattern stats
    pattern_fields = [
        "pattern_double_bottom", "pattern_bullish_engulfing", "pattern_bearish_engulfing",
        "pattern_bull_flag", "pattern_rsi_divergence", "pattern_macd_crossover",
        "pattern_volume_surge", "pattern_head_and_shoulders",
    ]
    pattern_counts = {}
    for field in pattern_fields:
        count = await db["features_snapshots"].count_documents({field: True})
        pattern_counts[field] = count

    # Tier activity
    alerts_count = await db["ml_alerts"].count_documents({})
    paper_trades = await db["ml_paper_trades"].count_documents({})
    live_trades = await db["ml_live_trades"].count_documents({})

    stats = _load_model_stats()
    backtest = _load_latest_backtest()

    return {
        "snapshots": {"total": total_snapshots, "labeled": labeled, "unlabeled": unlabeled, "errors": errors},
        "patterns": pattern_counts,
        "model": {"trained": stats is not None, "stats": stats.model_dump() if stats else None},
        "backtest": backtest.model_dump() if backtest else None,
        "activity": {"alerts": alerts_count, "paper_trades": paper_trades, "live_trades": live_trades},
        "milestones": {
            "first_train": labeled >= 100,
            "first_backtest": labeled >= 200,
            "pattern_significance": labeled >= 300,
            "tier1_eligible": labeled >= 500,
            "tier3_eligible": labeled >= 1000,
        },
    }


async def run_autonomous_actions(
    signal: SignalResult,
    database: AsyncIOMotorDatabase,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Orchestrate all 3 tiers based on current gate status."""
    results: dict[str, Any] = {"tier1": None, "tier2": None, "tier3": None}

    stats = _load_model_stats()
    if not stats:
        return results

    backtest = _load_latest_backtest()
    days_live = await _get_days_live(database)

    # Tier 1: Alerts
    try:
        from services.ml_alert_service import maybe_send_alert
        alert = await maybe_send_alert(signal, stats, database, user_id)
        if alert:
            results["tier1"] = {"action": "alert_sent", "ticker": signal.ticker}
    except Exception as exc:
        logger.warning("Tier 1 alert failed: %s", exc)

    # Tier 2: Paper Trading
    if backtest:
        try:
            from services.ml_paper_trader import maybe_paper_trade
            trade = await maybe_paper_trade(signal, stats, backtest, database)
            if trade:
                results["tier2"] = {"action": "paper_trade", "ticker": signal.ticker,
                                     "shares": trade["shares"], "dollar_amount": trade["dollar_amount"]}
        except Exception as exc:
            logger.warning("Tier 2 paper trade failed: %s", exc)

    # Tier 3: Live Execution
    if backtest:
        try:
            # Check user opt-in from database
            user_opt_in = False
            if user_id:
                user_doc = await database["users"].find_one(
                    {"_id": __import__("bson").ObjectId(user_id)},
                    {"ml_live_trading_opt_in": 1},
                )
                user_opt_in = bool(user_doc and user_doc.get("ml_live_trading_opt_in"))

            from services.ml_alpaca_broker import maybe_live_trade
            live = await maybe_live_trade(
                signal, stats, backtest, days_live, database, user_opt_in,
            )
            if live:
                results["tier3"] = {"action": "live_order", "ticker": signal.ticker,
                                     "side": live["side"], "shares": live["shares"]}
        except Exception as exc:
            logger.warning("Tier 3 live trade failed: %s", exc)

    return results
