"""
Day-Trade Exit Monitor — closes day-trade positions that hit their
``max_hold_until`` timer, regardless of P&L.

The scanner stamps ``max_hold_until=21:00 UTC`` on every queued
target. If the executor (paper-trade write path) propagates that
field onto the resulting trade row, this monitor detects the
breach and closes the row at the latest available price.

Why a separate module
─────────────────────
Time-based exits are an orthogonal concern from price-based stops
(``services.crypto_paper_trader._maybe_close``) and signal-flip
reversals (``services.terminal_aggregator``). Keeping the
day-trade EOD timer here means existing closer logic doesn't need
to learn about a new exit reason.

Idempotent
──────────
Every call walks every open day-trade row whose ``max_hold_until``
is in the past and emits a CLOSE event. A re-run after the close
is a no-op since the row is no longer open.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def expire_due_day_trades(db: Any) -> dict[str, int]:
    """Walk both paper-trade collections, find rows tagged
    ``source="day_trade_scanner"`` whose ``max_hold_until`` has
    passed, and close them.

    Closing strategy: stamp ``status="closed"`` + ``closed_at`` +
    ``close_reason="day_trade_eod_timer"``. P&L resolution flows
    through the existing closer, which reads the open row's last-
    known mid price.

    Returns ``{"equity_closed": N, "crypto_closed": N, "errors": N}``.
    Never raises.
    """
    if db is None:
        return {"equity_closed": 0, "crypto_closed": 0, "errors": 0}
    now = datetime.now(timezone.utc)
    counts = {"equity_closed": 0, "crypto_closed": 0, "errors": 0}

    for coll, key in (
        ("paper_trades", "equity_closed"),
        ("crypto_paper_trades", "crypto_closed"),
    ):
        try:
            cursor = db[coll].find(
                {
                    "source": "day_trade_scanner",
                    "status": "open",
                    "max_hold_until": {"$lte": now},
                },
                {"_id": 0, "trade_id": 1, "symbol": 1, "ticker": 1},
            ).limit(500)
            rows = await cursor.to_list(length=500)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[day-trade-exit] %s read failed: %s", coll, exc,
            )
            counts["errors"] += 1
            continue

        for r in rows:
            try:
                await db[coll].update_one(
                    {"trade_id": r["trade_id"], "status": "open"},
                    {"$set": {
                        "status": "closed",
                        "closed_at": now,
                        "close_reason": "day_trade_eod_timer",
                    }},
                )
                counts[key] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[day-trade-exit] %s close failed for %s: %s",
                    coll, r.get("trade_id"), exc,
                )
                counts["errors"] += 1
    return counts
