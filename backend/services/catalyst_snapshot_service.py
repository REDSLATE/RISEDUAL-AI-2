"""
Catalyst snapshot service — projects the output of
``news_shock_service`` onto the ``catalyst_snapshots`` collection
(single doc per symbol, upserted each tick).

``catalyst_snapshots`` is the one read by the IP contract,
conviction service, Commander narrative builder, and Terminal
aggregator. Keeping the projection separate from the compute step
keeps the compute step pure (easy to test) and lets the snapshot
collection own the "event risk" derivation — a strictly additive
label on top of the raw shock state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from services.news_shock_service import (
    compute_news_shock_batch,
    compute_news_shock_for_symbol,
)

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def event_risk_from_news_shock(shock_state: str, sentiment_label: str) -> str:
    """Project shock state onto a 3-level event risk used by the
    IP contract. Sentiment is accepted as a parameter for future
    extensions (e.g., a bearish-high gets RESTRICTED while a
    bullish-high gets ELEVATED). For now they collapse to the
    same rule — matching the spec verbatim."""
    if shock_state == "high":
        return "restricted"
    if shock_state == "elevated":
        return "elevated"
    return "normal"


async def upsert_catalyst_snapshot(db: Any, shock: dict[str, Any]) -> None:
    symbol = shock["symbol"]
    event_risk = event_risk_from_news_shock(
        shock_state=shock.get("shock_state", "normal"),
        sentiment_label=shock.get("sentiment_label", "unknown"),
    )
    await db.catalyst_snapshots.update_one(
        {"symbol": symbol},
        {
            "$set": {
                "symbol": symbol,
                "news_shock": shock,
                "event_risk": event_risk,
                "updated_at": shock.get("computed_at") or utcnow(),
            }
        },
        upsert=True,
    )


async def refresh_catalyst_snapshot_for_symbol(
    db: Any,
    symbol: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    shock = await compute_news_shock_for_symbol(db=db, symbol=symbol, now=now)
    try:
        await upsert_catalyst_snapshot(db, shock)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[catalyst] snapshot upsert failed for %s (non-critical): %s",
            symbol, exc,
        )
    return shock


async def refresh_catalyst_snapshots(
    db: Any,
    symbols: list[str],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Batch refresh — compute + upsert for every symbol. Failures
    are swallowed per-symbol so one broken row doesn't prevent the
    rest of the batch from landing."""
    shocks = await compute_news_shock_batch(db=db, symbols=symbols, now=now)
    for shock in shocks:
        try:
            await upsert_catalyst_snapshot(db, shock)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[catalyst] snapshot upsert failed for %s (non-critical): %s",
                shock.get("symbol"), exc,
            )
    return shocks
