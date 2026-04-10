"""Whale Radar SSE endpoint — streams whale events from multiple crypto pairs simultaneously."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from services.orderflow_ws_service import stream_manager, CRYPTO_TICKERS, WHALE_INTENSITY_THRESHOLD

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

RADAR_TICKERS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "DOT", "LINK", "SHIB"]


@router.get("/stream/whale-radar")
async def whale_radar_sse(request: Request):
    """Stream whale wall events from top 10 crypto pairs via a single SSE connection."""
    queues = {}

    async def event_generator():
        try:
            # Subscribe to all tickers
            for ticker in RADAR_TICKERS:
                q = asyncio.Queue(maxsize=20)
                queues[ticker] = q
                await stream_manager.subscribe(ticker, q)

            logger.info(f"Whale Radar: subscribed to {len(RADAR_TICKERS)} tickers")

            # Send initial status
            yield {
                "event": "radar_status",
                "data": json.dumps({
                    "type": "radar_status",
                    "tickers": RADAR_TICKERS,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }),
            }

            while True:
                if await request.is_disconnected():
                    break

                # Poll all queues with short timeout
                got_data = False
                for ticker, q in queues.items():
                    try:
                        msg = q.get_nowait()
                        snap = json.loads(msg) if isinstance(msg, str) else msg
                        if snap.get("type") != "snapshot":
                            continue

                        # Only emit whale walls (intensity >= threshold)
                        whales = [w for w in snap.get("walls", []) if w.get("intensity", 0) >= WHALE_INTENSITY_THRESHOLD]

                        if whales:
                            yield {
                                "event": "whale",
                                "data": json.dumps({
                                    "type": "whale",
                                    "ticker": ticker,
                                    "mid": snap.get("mid"),
                                    "bias": snap.get("bias"),
                                    "bid_pct": snap.get("bid_pct"),
                                    "walls": whales,
                                    "ts": snap.get("ts"),
                                }),
                            }
                            got_data = True

                        # Always emit a lightweight summary for heatmap
                        yield {
                            "event": "tick",
                            "data": json.dumps({
                                "type": "tick",
                                "ticker": ticker,
                                "mid": snap.get("mid"),
                                "bias": snap.get("bias"),
                                "bid_pct": snap.get("bid_pct"),
                                "wall_count": len(snap.get("walls", [])),
                                "whale_count": len(whales),
                                "ts": snap.get("ts"),
                            }),
                        }
                        got_data = True
                    except asyncio.QueueEmpty:
                        continue

                if not got_data:
                    await asyncio.sleep(0.5)
                    # Keepalive every 2s
                    yield {"event": "ping", "data": ""}

        except asyncio.CancelledError:
            pass
        finally:
            for ticker, q in queues.items():
                await stream_manager.unsubscribe(ticker, q)
            logger.info("Whale Radar: disconnected, unsubscribed all tickers")

    return EventSourceResponse(event_generator())
