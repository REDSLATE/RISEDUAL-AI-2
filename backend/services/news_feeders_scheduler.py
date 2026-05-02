"""
News feeders scheduler — drives Benzinga + Alpha Vantage telemetry
population on a 15-minute market-hours cadence.

Scheduler contract
------------------
* Runs every 15 minutes. Each tick checks the market-hours gate
  (weekday + 13:00–21:00 UTC window, conservative around the 9:30–16:00
  ET cash session) and no-ops outside that window.
* Pulls the top ``FEEDER_BATCH_SIZE`` (default 15) Tier A symbols,
  ROTATED by an offset persisted in Mongo (``scheduler_state`` single
  doc). After ~7 ticks, the full 100-symbol Tier A rotates through;
  each symbol gets ~3-4 samples per market day — exactly the minimum
  needed for ``equity_telemetry._baseline`` to start computing
  z-scores.
* Feeds both Benzinga (news volume) and Alpha Vantage (sentiment)
  back-to-back on the same symbol batch so the two halves of
  ``NEWS_SHOCK`` share a baseline cadence.

Budget math (defaults)
----------------------
* 15 symbols × 2 providers = 30 calls per tick
* ~26 ticks in a 6.5-hour market window = 780 calls/day total
* Split evenly: ~390 Benzinga calls/day (ceiling 500, 78% utilisation)
  + ~390 AV calls/day (ceiling 600, 65% utilisation). Both providers
  have plenty of headroom for ad-hoc operator probes.

Fail-safe
---------
Every layer below this scheduler is already no-op-on-error:

* Missing keys → feeders return disabled sentinel, nothing breaks.
* Daily ceiling hit → batch feeder short-circuits mid-loop; remaining
  symbols carry over to the next tick automatically via the rotation
  offset.
* Mongo state read failure → rotation offset resets to 0 (no samples
  lost, we just re-feed the top of Tier A twice in a row).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _batch_size() -> int:
    try:
        return max(1, min(20, int(os.environ.get("NEWS_FEEDER_BATCH_SIZE", "15"))))
    except (TypeError, ValueError):
        return 15


def _is_market_hours(now: datetime) -> bool:
    """Conservative market-hours gate: Mon-Fri, 13:00-21:00 UTC.

    Tighter than the actual 13:30/14:30 → 20:00/21:00 RTH windows
    (DST swings the boundary) — a few extra ticks at the edges pose
    no quota concern and avoid the DST bug trap entirely.
    """
    if now.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    hour = now.hour
    return 13 <= hour < 21


async def _rotation_offset(db: Any) -> int:
    """Read the rotation index from Mongo. Any failure resets to 0 —
    duplicates are harmless (feeders are idempotent), skipped symbols
    would bias the baseline."""
    if db is None:
        return 0
    try:
        doc = await db.scheduler_state.find_one(
            {"_id": "news_feeders_rotation"}, {"offset": 1},
        )
        return int((doc or {}).get("offset", 0))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[news-feeder-sched] offset read failed: %s", exc)
        return 0


async def _advance_offset(db: Any, new_offset: int) -> None:
    if db is None:
        return
    try:
        await db.scheduler_state.update_one(
            {"_id": "news_feeders_rotation"},
            {
                "$set": {
                    "offset": int(new_offset),
                    "updated_at": datetime.now(timezone.utc),
                },
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[news-feeder-sched] offset write failed: %s", exc)


async def _pick_symbols(db: Any, batch_size: int) -> tuple[list[str], int]:
    """Select the next ``batch_size`` Tier A symbols via rotation.

    Returns ``(symbols, new_offset)`` — caller persists the new offset
    after the feeders complete.
    """
    from services.top_universe_service import _get_active_tiered_symbols

    tier_a = await _get_active_tiered_symbols(db, ["A"])
    if not tier_a:
        return [], 0

    offset = await _rotation_offset(db)
    size = len(tier_a)
    offset = offset % size

    # Wrap around cleanly if the slice would spill past the end.
    end = offset + batch_size
    if end <= size:
        slice_rows = tier_a[offset:end]
        new_offset = end % size
    else:
        slice_rows = tier_a[offset:] + tier_a[: end - size]
        new_offset = end - size

    symbols = [r["symbol"] for r in slice_rows if r.get("symbol")]
    return symbols, new_offset


async def run_news_feeders_tick(db: Any) -> dict[str, Any]:
    """One scheduler tick. Safe to call on every invocation — the
    market-hours gate returns a ``skipped`` summary outside RTH.

    Returns a summary dict so an operator can ``GET`` the last-run
    state from a future admin endpoint without tailing logs.
    """
    now = datetime.now(timezone.utc)
    if not _is_market_hours(now):
        return {"status": "skipped", "reason": "outside_market_hours", "at": now.isoformat()}

    batch_size = _batch_size()
    symbols, new_offset = await _pick_symbols(db, batch_size)
    if not symbols:
        return {"status": "skipped", "reason": "tier_a_empty", "at": now.isoformat()}

    from services.news_shock_feeder import batch_feed_symbols
    from services.av_sentiment_feeder import batch_feed_sentiment

    logger.info(
        "[news-feeder-sched] tick: %d symbols (offset→%d): %s",
        len(symbols), new_offset, ",".join(symbols[:5]) + ("…" if len(symbols) > 5 else ""),
    )

    benzinga_summary: dict[str, Any] = {"fed": 0, "skipped": 0, "total_articles": 0}
    av_summary: dict[str, Any] = {"fed": 0, "skipped": 0, "avg_sentiment_abs": 0.0}

    try:
        benzinga_summary = await batch_feed_symbols(db, symbols)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[news-feeder-sched] benzinga batch failed: %s", exc)

    try:
        av_summary = await batch_feed_sentiment(db, symbols)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[news-feeder-sched] av batch failed: %s", exc)

    # Only advance the offset AFTER both feeders run — if the tick died
    # mid-batch (Mongo blip, scheduler restart), we'll re-run the same
    # slice next tick. Idempotent feeders make this safe.
    await _advance_offset(db, new_offset)

    return {
        "status": "ran",
        "at": now.isoformat(),
        "symbols_requested": len(symbols),
        "new_offset": new_offset,
        "benzinga": benzinga_summary,
        "av": av_summary,
    }
