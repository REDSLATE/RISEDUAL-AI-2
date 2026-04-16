"""Alert Service (Tier 1) — dispatches push alerts when model confidence is high.

Activated when CalibrationGate.is_alert_ready() returns True.
Sends alerts via existing VAPID push notifications and stores in MongoDB.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from risedual_core.ml.calibration_gate import is_alert_ready, kelly_fraction
from risedual_core.schemas.market import CalibrationStats, SignalResult

logger = logging.getLogger(__name__)
ALERTS_COLLECTION = "ml_alerts"
MIN_SIGNAL_CONFIDENCE = 0.60


async def maybe_send_alert(
    signal: SignalResult,
    stats: CalibrationStats,
    db: AsyncIOMotorDatabase,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """Check gate conditions and send alert if warranted.

    Returns the alert document if sent, None if suppressed.
    """
    if not is_alert_ready(stats):
        return None

    if signal.confidence < MIN_SIGNAL_CONFIDENCE:
        return None

    if not signal.patterns_detected:
        return None

    # Regime check: don't alert bullish in bear regime or bearish in bull
    if signal.regime and signal.direction.value == "up" and signal.regime == "bear":
        return None
    if signal.regime and signal.direction.value == "down" and signal.regime == "bull":
        return None

    alert_doc = {
        "ticker": signal.ticker,
        "direction": signal.direction.value,
        "confidence": signal.confidence,
        "regime": signal.regime,
        "patterns": signal.patterns_detected,
        "model_version": signal.model_version,
        "kelly_fraction": kelly_fraction(signal.confidence),
        "user_id": user_id,
        "created_at": datetime.now(timezone.utc),
        "delivered": False,
        "tier": 1,
    }

    try:
        await db[ALERTS_COLLECTION].insert_one(alert_doc)
        logger.info(
            "ML Alert: %s %s @ %.1f%% confidence, patterns=%s",
            signal.ticker, signal.direction.value,
            signal.confidence * 100, signal.patterns_detected,
        )
    except Exception:
        logger.exception("Failed to persist ML alert for %s", signal.ticker)
        return None

    # Dispatch via existing push notification system
    try:
        from services.push_service import send_push_to_user
        if user_id:
            title = f"RISEDUAL Signal: {signal.ticker} {signal.direction.value.upper()}"
            body = (
                f"{signal.confidence:.0%} confidence | "
                f"Patterns: {', '.join(signal.patterns_detected)} | "
                f"Regime: {signal.regime or 'unknown'}"
            )
            await send_push_to_user(db, user_id, title, body)
    except Exception:
        logger.debug("Push notification not available for ML alerts")

    return alert_doc
