"""SSE endpoint for real-time order flow streaming.

Backend connects to Binance WS internally and relays processed
snapshots to frontend via Server-Sent Events (compatible with K8s ingress).
"""

import asyncio
import json
import logging
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from services.orderflow_ws_service import stream_manager, CRYPTO_TICKERS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/stream/orderflow/{symbol}")
async def orderflow_sse(request: Request, symbol: str):
    """Stream real-time order book depth updates via SSE for crypto tickers."""
    clean = symbol.upper().replace("-USD", "").replace("USDT", "")

    if clean not in CRYPTO_TICKERS:
        return {"error": f"{symbol} is not a supported crypto ticker"}

    queue = asyncio.Queue(maxsize=30)

    async def event_generator():
        try:
            # Send cached history first
            history = stream_manager.get_history(clean)
            if history:
                yield {
                    "event": "history",
                    "data": json.dumps({"type": "history", "snapshots": history[-30:]}),
                }

            # Subscribe to live stream
            await stream_manager.subscribe(clean, queue)
            logger.info(f"SSE client connected for {clean}")

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=2.0)
                    yield {"event": "snapshot", "data": msg}
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield {"event": "ping", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            await stream_manager.unsubscribe(clean, queue)
            logger.info(f"SSE client disconnected for {clean}")

    return EventSourceResponse(event_generator())
