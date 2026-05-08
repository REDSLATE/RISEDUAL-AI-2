"""WebSocket endpoint for real-time order flow streaming."""

import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.orderflow_ws_service import stream_manager, CRYPTO_TICKERS

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/api/ws/orderflow/{symbol}")
async def orderflow_ws(websocket: WebSocket, symbol: str):
    """Stream real-time order book depth updates for crypto tickers."""
    clean = symbol.upper().replace("-USD", "").replace("USDT", "")

    if clean not in CRYPTO_TICKERS:
        await websocket.close(code=4000, reason=f"{symbol} is not a supported crypto ticker")
        return

    await websocket.accept()
    queue = asyncio.Queue(maxsize=30)

    try:
        # Send cached history first (so heatmap populates immediately)
        history = stream_manager.get_history(clean)
        if history:
            await websocket.send_text(json.dumps({"type": "history", "snapshots": history}))

        # Subscribe to live stream
        await stream_manager.subscribe(clean, queue)
        logger.info(f"WS client connected for {clean}")

        while True:
            msg = await queue.get()
            await websocket.send_text(msg)

    except WebSocketDisconnect:
        logger.info(f"WS client disconnected for {clean}")
    except Exception as e:
        logger.warning(f"WS error for {clean}: {e}")
    finally:
        await stream_manager.unsubscribe(clean, queue)
