"""Real-Time Insight Stream — Server-Sent Events (SSE) endpoint.

Pushes live events to the frontend:
- new_verification: When a prediction is verified (hit or miss)
- post_mortem: When AI post-mortem completes with failure classification
- toxic_alert: When nightly cleanup detects toxic spikes
- memory_update: When vector memory is trained or cleaned
"""

import json
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["stream"])

db = None


def set_db(database):
    global db
    db = database


# In-memory event buffer (lightweight, no external deps)
_event_buffer = []
_MAX_BUFFER = 100


def push_event(event_type: str, data: dict):
    """Push an event to the buffer from anywhere in the backend."""
    event = {
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _event_buffer.append(event)
    if len(_event_buffer) > _MAX_BUFFER:
        _event_buffer.pop(0)


@router.get("/insights")
async def insight_stream(request: Request):
    """SSE endpoint — streams real-time trading insights to the frontend.

    Events:
    - new_verification: {ticker, direction, correct, failure_code, price_at, price_now}
    - post_mortem: {ticker, failure_code, reasoning, key_headline, source}
    - toxic_alert: {toxic_count, affected_tickers, total_before, total_after}
    - memory_update: {action, episodes_count}
    """
    async def event_generator():
        last_idx = len(_event_buffer)
        last_db_check = datetime.now(timezone.utc)

        # Send initial heartbeat
        yield {"event": "connected", "data": json.dumps({
            "status": "connected",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })}

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            # 1. Flush any in-memory buffer events (pushed by other services)
            current_len = len(_event_buffer)
            if current_len > last_idx:
                for evt in _event_buffer[last_idx:current_len]:
                    yield {
                        "event": evt["type"],
                        "data": json.dumps(evt["data"]),
                    }
                last_idx = current_len

            # 2. Poll DB for recently verified predictions (every 15s)
            now = datetime.now(timezone.utc)
            if db is not None and (now - last_db_check).total_seconds() >= 15:
                last_db_check = now
                try:
                    await _poll_recent_events(now)
                except Exception as e:
                    logger.warning(f"SSE poll error: {e}")

            # 3. Send heartbeat every 30s to keep connection alive
            yield {"event": "heartbeat", "data": json.dumps({
                "timestamp": now.isoformat(),
                "buffer_size": current_len,
            })}

            await asyncio.sleep(10)

    return EventSourceResponse(event_generator())


async def _poll_recent_events(now: datetime):
    """Check MongoDB for recently completed events and push to buffer."""
    window = (now - timedelta(seconds=20)).isoformat()

    # Check for recent verifications
    cursor = db.predictions.find(
        {"verified_24h.verified_at": {"$gte": window}},
        {"_id": 0, "symbol": 1, "direction": 1, "confidence": 1,
         "price_at_prediction": 1, "verified_24h": 1},
    ).limit(5)

    async for pred in cursor:
        v = pred.get("verified_24h", {})
        event_id = f"v24h_{pred.get('symbol')}_{v.get('verified_at', '')[:19]}"

        # Deduplicate: check if already in buffer
        if any(e["data"].get("event_id") == event_id for e in _event_buffer[-20:]):
            continue

        push_event("new_verification", {
            "event_id": event_id,
            "ticker": pred.get("symbol"),
            "direction": pred.get("direction"),
            "confidence": pred.get("confidence", 0),
            "correct": v.get("correct", False),
            "failure_code": v.get("failure_code"),
            "failure_reason": v.get("failure_reason", ""),
            "price_at": pred.get("price_at_prediction", 0),
            "price_now": v.get("price", 0),
            "verified_at": v.get("verified_at", ""),
        })

    # Check for recent post-mortems
    cursor2 = db.post_mortem_log.find(
        {"run_at": {"$gte": window}},
        {"_id": 0},
    ).limit(5)

    async for pm in cursor2:
        event_id = f"pm_{pm.get('ticker')}_{pm.get('run_at', '')[:19]}"
        if any(e["data"].get("event_id") == event_id for e in _event_buffer[-20:]):
            continue

        push_event("post_mortem", {
            "event_id": event_id,
            "ticker": pm.get("ticker"),
            "failure_code": pm.get("failure_code"),
            "heuristic_code": pm.get("heuristic_code"),
            "reasoning": pm.get("reasoning", ""),
            "key_headline": pm.get("key_headline"),
            "source": pm.get("source", "heuristic"),
        })


@router.get("/recent")
async def recent_events(limit: int = 20):
    """Get recent events from the buffer (for clients that missed SSE events)."""
    events = _event_buffer[-limit:]
    return {"events": list(reversed(events)), "total_buffered": len(_event_buffer)}
