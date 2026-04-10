"""Real-time Order Flow WebSocket Service.

Connects to Binance US WebSocket depth stream, processes L2 updates,
detects wall movements, and relays processed snapshots to frontend clients.

Data flow:
  Binance WS → OrderFlowStream → FastAPI WebSocket → Frontend Heatmap
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Set

import numpy as np
import websockets

logger = logging.getLogger(__name__)

BINANCE_WS_BASE = "wss://stream.binance.us:9443/ws"
WALL_MULTIPLIER = 3.0
SIGNIFICANT_WALL = 5.0
MAX_HISTORY = 60  # Keep 60 snapshots (~60s at 1s intervals)
WHALE_INTENSITY_THRESHOLD = 85
WHALE_COOLDOWN_SECONDS = 300  # Max 1 alert per ticker per 5 min

CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "DOT", "SHIB", "LINK"}


def _ratio_to_intensity(ratio: float) -> int:
    """Convert volume ratio (vs median) to 0-100 intensity score.
    ratio 1x = 0, ratio 3x = 30, ratio 10x = ~85, ratio 20x+ = 100."""
    return min(int(((ratio - 1) / 9.0) * 100), 100) if ratio >= 1 else 0



def _binance_symbol(ticker: str) -> str:
    clean = ticker.upper().replace("-USD", "").replace("USDT", "")
    return f"{clean.lower()}usdt"


class OrderFlowStream:
    """Manages shared Binance WebSocket connections and dispatches to subscribers."""

    def __init__(self):
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._tasks: Dict[str, asyncio.Task] = {}
        self._prev_snapshot: Dict[str, dict] = {}
        self._history: Dict[str, list] = defaultdict(list)
        self._whale_cooldowns: Dict[str, datetime] = {}  # "BTC:71800" -> last alert time
        self._lock = asyncio.Lock()
        self._db = None  # Set by server.py for push notifications

    def set_db(self, db):
        """Attach MongoDB reference for push notifications."""
        self._db = db

    async def subscribe(self, symbol: str, queue: asyncio.Queue):
        """Add a subscriber queue for a symbol. Starts Binance stream if first."""
        key = symbol.upper()
        async with self._lock:
            self._subscribers[key].add(queue)
            if key not in self._tasks or self._tasks[key].done():
                self._tasks[key] = asyncio.create_task(self._run_stream(key))
                logger.info(f"Started Binance WS stream for {key}")

    async def unsubscribe(self, symbol: str, queue: asyncio.Queue):
        """Remove subscriber. Stops Binance stream if last subscriber leaves."""
        key = symbol.upper()
        async with self._lock:
            self._subscribers[key].discard(queue)
            if not self._subscribers[key]:
                task = self._tasks.pop(key, None)
                if task and not task.done():
                    task.cancel()
                self._prev_snapshot.pop(key, None)
                self._history.pop(key, None)
                logger.info(f"Stopped Binance WS stream for {key} (no subscribers)")

    def get_history(self, symbol: str) -> list:
        """Return cached snapshot history for initial load."""
        return list(self._history.get(symbol.upper(), []))

    async def _run_stream(self, symbol: str):
        """Connect to Binance depth stream with auto-reconnect."""
        binance_sym = _binance_symbol(symbol)
        stream_url = f"{BINANCE_WS_BASE}/{binance_sym}@depth20@1000ms"

        while True:
            try:
                async with websockets.connect(stream_url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"Connected to Binance WS: {stream_url}")
                    async for raw_msg in ws:
                        try:
                            data = json.loads(raw_msg)
                            snapshot = self._process_snapshot(symbol, data)
                            if snapshot:
                                self._cache_snapshot(symbol, snapshot)
                                await self._broadcast(symbol, snapshot)
                        except Exception as e:
                            logger.warning(f"Error processing WS message for {symbol}: {e}")
            except asyncio.CancelledError:
                logger.info(f"Binance WS cancelled for {symbol}")
                break
            except Exception as e:
                logger.warning(f"Binance WS disconnected for {symbol}: {e}. Reconnecting in 3s...")
                await asyncio.sleep(3)

    def _process_snapshot(self, symbol: str, data: dict) -> dict:
        """Process a partial depth snapshot into heatmap-ready format."""
        bids_raw = data.get("bids", [])
        asks_raw = data.get("asks", [])

        if not bids_raw or not asks_raw:
            return None

        bids = [{"price": float(b[0]), "qty": float(b[1]), "value": float(b[0]) * float(b[1])} for b in bids_raw]
        asks = [{"price": float(a[0]), "qty": float(a[1]), "value": float(a[0]) * float(a[1])} for a in asks_raw]

        all_values = [b["value"] for b in bids] + [a["value"] for a in asks]
        median_val = float(np.median(all_values)) if all_values else 1.0

        best_bid = bids[0]["price"]
        best_ask = asks[0]["price"]
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        spread_pct = (spread / mid_price) * 100 if mid_price > 0 else 0

        # Detect walls with 0-100 intensity
        walls = []
        whale_candidates = []
        for level in bids:
            ratio = level["value"] / median_val if median_val > 0 else 0
            if ratio >= WALL_MULTIPLIER:
                intensity = _ratio_to_intensity(ratio)
                wall = {
                    "price": round(level["price"], 2),
                    "qty": round(level["qty"], 6),
                    "value": round(level["value"], 2),
                    "ratio": round(ratio, 1),
                    "intensity": intensity,
                    "side": "bid",
                    "strength": "major" if ratio >= SIGNIFICANT_WALL else "minor",
                }
                walls.append(wall)
                if intensity >= WHALE_INTENSITY_THRESHOLD:
                    whale_candidates.append(wall)

        for level in asks:
            ratio = level["value"] / median_val if median_val > 0 else 0
            if ratio >= WALL_MULTIPLIER:
                intensity = _ratio_to_intensity(ratio)
                wall = {
                    "price": round(level["price"], 2),
                    "qty": round(level["qty"], 6),
                    "value": round(level["value"], 2),
                    "ratio": round(ratio, 1),
                    "intensity": intensity,
                    "side": "ask",
                    "strength": "major" if ratio >= SIGNIFICANT_WALL else "minor",
                }
                walls.append(wall)
                if intensity >= WHALE_INTENSITY_THRESHOLD:
                    whale_candidates.append(wall)

        # Trigger whale alerts for intensity >= 85
        if whale_candidates:
            asyncio.ensure_future(self._trigger_whale_alerts(symbol, whale_candidates))

        # Compute bias
        total_bid_val = sum(b["value"] for b in bids)
        total_ask_val = sum(a["value"] for a in asks)
        total = total_bid_val + total_ask_val
        bid_pct = (total_bid_val / total * 100) if total > 0 else 50

        if bid_pct > 58:
            bias = "INSTITUTIONAL_BID"
        elif bid_pct < 42:
            bias = "INSTITUTIONAL_ASK"
        else:
            bias = "BALANCED"

        # Detect wall events (appeared / vanished) by comparing to previous snapshot
        wall_events = self._detect_wall_events(symbol, walls)

        # Build heatmap levels: log-scaled intensity for visible heatmap
        max_val = max(all_values) if all_values else 1
        log_max = np.log1p(max_val)
        heatmap_bids = [
            {"price": round(b["price"], 2), "intensity": float(np.log1p(b["value"]) / log_max) if log_max > 0 else 0, "qty": round(b["qty"], 6)}
            for b in bids
        ]
        heatmap_asks = [
            {"price": round(a["price"], 2), "intensity": float(np.log1p(a["value"]) / log_max) if log_max > 0 else 0, "qty": round(a["qty"], 6)}
            for a in asks
        ]

        snapshot = {
            "type": "snapshot",
            "symbol": symbol,
            "ts": datetime.now(timezone.utc).isoformat(),
            "mid": round(mid_price, 2),
            "spread": round(spread, 2),
            "spread_pct": round(spread_pct, 4),
            "best_bid": round(best_bid, 2),
            "best_ask": round(best_ask, 2),
            "bid_total": round(total_bid_val, 2),
            "ask_total": round(total_ask_val, 2),
            "bid_pct": round(bid_pct, 1),
            "bias": bias,
            "bids": heatmap_bids,
            "asks": heatmap_asks,
            "walls": walls,
            "wall_events": wall_events,
            "median_val": round(median_val, 2),
        }

        # Store for wall diff
        self._prev_snapshot[symbol] = {
            "wall_prices": {(w["price"], w["side"]) for w in walls}
        }

        return snapshot

    def _detect_wall_events(self, symbol: str, current_walls: list) -> list:
        """Compare current walls to previous snapshot to detect appearances/vanishes."""
        prev = self._prev_snapshot.get(symbol)
        if not prev:
            return []

        prev_prices = prev.get("wall_prices", set())
        curr_prices = {(w["price"], w["side"]) for w in current_walls}
        curr_map = {(w["price"], w["side"]): w for w in current_walls}

        events = []
        # New walls
        for key in curr_prices - prev_prices:
            w = curr_map[key]
            events.append({"event": "appeared", "price": w["price"], "side": w["side"], "value": w["value"], "ratio": w["ratio"]})

        # Vanished walls
        for key in prev_prices - curr_prices:
            events.append({"event": "vanished", "price": key[0], "side": key[1]})

        return events

    async def _trigger_whale_alerts(self, symbol: str, whale_walls: list):
        """Send push notifications for whale walls (intensity >= 85), with cooldown."""
        if not self._db:
            return
        now = datetime.now(timezone.utc)
        for w in whale_walls:
            key = f"{symbol}:{int(w['price'])}"
            last = self._whale_cooldowns.get(key)
            if last and (now - last).total_seconds() < WHALE_COOLDOWN_SECONDS:
                continue  # Cooldown active
            self._whale_cooldowns[key] = now
            try:
                from services.push_service import notify_whale_wall
                await notify_whale_wall(
                    self._db,
                    ticker=symbol,
                    side=w["side"],
                    price=w["price"],
                    intensity=w["intensity"],
                )
                logger.info(f"Whale alert sent: {symbol} {w['side']} wall at ${w['price']} (intensity {w['intensity']})")
            except Exception as e:
                logger.warning(f"Whale alert failed: {e}")

    def _cache_snapshot(self, symbol: str, snapshot: dict):
        """Store in rolling history buffer."""
        history = self._history[symbol]
        history.append(snapshot)
        if len(history) > MAX_HISTORY:
            self._history[symbol] = history[-MAX_HISTORY:]

    async def _broadcast(self, symbol: str, snapshot: dict):
        """Send snapshot to all subscribers for this symbol."""
        msg = json.dumps(snapshot)
        dead_queues = []
        for queue in list(self._subscribers.get(symbol, [])):
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                dead_queues.append(queue)

        # Prune overflowed queues
        for q in dead_queues:
            self._subscribers[symbol].discard(q)


# Singleton instance
stream_manager = OrderFlowStream()
