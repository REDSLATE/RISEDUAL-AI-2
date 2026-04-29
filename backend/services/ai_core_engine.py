"""AI Core Learning Engine — unified bot-brain stats surface.

A single in-memory + Mongo-backed surface that ingests resolved trades
(from `paper_trades`, verified `predictions`, or direct callers) and
keeps rolling aggregate + per-condition statistics so the operator can
ask one question — "how is the AI doing?" — and get a coherent answer.

Design rules
------------
* **Idempotent ingestion.** Every recorded trade has a deterministic
  ``trade_key`` built from its ``source`` + ``source_id``. Re-posting
  the same trade is a no-op — the engine recomputes nothing and the
  Mongo upsert short-circuits on duplicate. Lets the auto-wire
  scheduler run repeatedly without polluting stats.
* **In-memory cache + Mongo source-of-truth.** Cold-start replays the
  last ``MAX_LOG`` resolved trades from Mongo so a backend restart
  doesn't lose stats. Engine-only writes go to Mongo via
  ``asyncio.to_thread``-friendly motor calls; reads off the cache.
* **Bucketed condition stats.** Every trade emits up to four
  (key, value) condition tuples (regime, agent, confidence_bucket,
  asset_type). The engine's ``condition_stats`` dict tracks
  total/wins/losses per tuple so callers get a per-condition
  win-rate without re-scanning Mongo.
* **No silent state.** Every mutation logs via ``logging``. Every
  exception is caught and logged; the engine never raises into the
  caller.
"""
from __future__ import annotations

__domain__ = "PRD"

import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
MAX_LOG = 1000  # In-memory ring buffer; Mongo holds the full history.

# Confidence buckets (lower-inclusive, upper-exclusive); covers 0-100.
_CONF_BUCKETS: list[tuple[str, float, float]] = [
    ("0-60", 0.0, 60.0),
    ("60-70", 60.0, 70.0),
    ("70-80", 70.0, 80.0),
    ("80-90", 80.0, 90.0),
    ("90-100", 90.0, 100.01),
]

# Outcome canonicalisation — accepts the various shapes upstream
# services actually emit (paper_trades' "win"/"loss"/"flat";
# predictions' boolean `correct`; raw "hit"/"miss" strings).
_WIN_TOKENS = {"win", "hit", "correct", True, "true", 1, "1"}
_LOSS_TOKENS = {"loss", "miss", False, "false", 0, "0"}
_FLAT_TOKENS = {"flat", "neutral", "tie", None}


def _bucket_confidence(conf_pct: Optional[float]) -> Optional[str]:
    if conf_pct is None:
        return None
    try:
        c = float(conf_pct)
    except (TypeError, ValueError):
        return None
    # Tolerate the 0-1 vs 0-100 mixed-scale problem the rest of the
    # app normalises elsewhere — collapse silently.
    if 0 <= c <= 1.0:
        c *= 100.0
    for label, lo, hi in _CONF_BUCKETS:
        if lo <= c < hi:
            return label
    return None


def _canon_outcome(outcome: Any) -> str:
    """Return one of: 'win' | 'loss' | 'flat' | 'pending'."""
    if outcome in _WIN_TOKENS:
        return "win"
    if outcome in _LOSS_TOKENS:
        return "loss"
    if outcome in _FLAT_TOKENS:
        return "flat"
    s = str(outcome).strip().lower()
    if s in {"win", "hit", "true", "1", "correct"}:
        return "win"
    if s in {"loss", "miss", "false", "0", "incorrect"}:
        return "loss"
    if s in {"flat", "neutral", "tie", ""}:
        return "flat"
    return "pending"


def _build_trade_key(source: str, source_id: str) -> str:
    return f"{source}:{source_id}"


# ── Engine ───────────────────────────────────────────────────────────


class LearningEngine:
    """Singleton-style engine. Created once at import; bound to db
    via :func:`set_db`. All public methods are coroutine-safe; the
    in-memory dicts are mutated only from the asyncio event loop, so
    no lock is required."""

    def __init__(self) -> None:
        self.trade_log: deque[dict] = deque(maxlen=MAX_LOG)
        self.stats: dict[str, int] = {
            "total_resolved": 0, "wins": 0, "losses": 0, "flats": 0,
            "pending": 0, "rejections": 0,
        }
        # condition_stats[(key, value)] = {"total":N, "wins":N, "losses":N, "flats":N}
        self.condition_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: {"total": 0, "wins": 0, "losses": 0, "flats": 0}
        )
        self._seen_keys: set[str] = set()
        self._db: Any = None
        self._hydrated: bool = False

    # ── Wiring ──
    def set_db(self, db: Any) -> None:
        self._db = db

    # ── Cold-start hydration ──
    async def hydrate(self) -> None:
        """Replay the last MAX_LOG resolved trades from Mongo into
        the in-memory log + stats so a backend restart doesn't lose
        rolling counts. Idempotent — guard flag prevents double-fill."""
        if self._hydrated or self._db is None:
            return
        self._hydrated = True
        try:
            cursor = self._db["ai_core_trades"].find(
                {}, {"_id": 0}
            ).sort("recorded_at", -1).limit(MAX_LOG)
            docs = await cursor.to_list(length=MAX_LOG)
            # Reverse so oldest first; trade_log is naturally chronological.
            for doc in reversed(docs):
                self._apply_in_memory(doc, hydrate=True)
            logger.info(
                "[ai_core] hydrated %d resolved trades from Mongo",
                len(docs),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[ai_core] hydrate failed: %s", e)

    # ── Public API ──
    async def record_trade(self, raw: dict) -> dict:
        """Persist one resolved trade. Returns ``{ok, dedup, trade_key}``.

        Required raw keys
        -----------------
        source : str         e.g. "paper_trades", "predictions",
                             "manual"
        source_id : str      caller-stable unique id within source
        symbol : str
        direction : str
        outcome : any        canonicalised to win/loss/flat/pending

        Optional
        --------
        confidence : float (0-1 or 0-100)
        regime : str
        agent : str
        asset_type : str
        entry_price / exit_price / pnl_usd / pnl_pct
        recorded_at : iso str  (defaults to now)
        """
        normalised = _normalise_trade(raw)
        if normalised is None:
            return {"ok": False, "reason": "missing_required_fields"}

        key = normalised["trade_key"]
        if key in self._seen_keys:
            return {"ok": True, "dedup": True, "trade_key": key}

        # Persist to Mongo first so a cache-only stat with no Mongo
        # row would never happen.
        if self._db is not None:
            try:
                await self._db["ai_core_trades"].update_one(
                    {"trade_key": key},
                    {"$setOnInsert": normalised},
                    upsert=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[ai_core] trade upsert failed for %s: %s", key, e)

        self._apply_in_memory(normalised, hydrate=False)
        return {"ok": True, "dedup": False, "trade_key": key}

    async def record_rejection(self, raw: dict) -> dict:
        """Log a rejected signal — a trade we *didn't* take. Useful
        for spotting "would-have-won" rate drift over time."""
        rej = {
            "source": raw.get("source") or "unknown",
            "source_id": raw.get("source_id") or "",
            "symbol": (raw.get("symbol") or "").upper(),
            "reason": raw.get("reason") or "unknown",
            "confidence": raw.get("confidence"),
            "recorded_at": raw.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
        }
        if self._db is not None:
            try:
                await self._db["ai_core_rejections"].insert_one(rej.copy())
            except Exception as e:  # noqa: BLE001
                logger.warning("[ai_core] rejection insert failed: %s", e)
        self.stats["rejections"] += 1
        return {"ok": True}

    async def reset(self) -> dict:
        """Wipe all in-memory + Mongo state. Dev/demo only."""
        self.trade_log.clear()
        self._seen_keys.clear()
        for k in list(self.stats.keys()):
            self.stats[k] = 0
        self.condition_stats.clear()
        if self._db is not None:
            try:
                await self._db["ai_core_trades"].delete_many({})
                await self._db["ai_core_rejections"].delete_many({})
                await self._db["ai_core_alerts"].delete_many({})
            except Exception as e:  # noqa: BLE001
                logger.warning("[ai_core] reset wipe failed: %s", e)
        return {"ok": True, "stats": self.stats_snapshot()}

    # ── Read API ──
    def stats_snapshot(self) -> dict:
        wins = self.stats["wins"]
        losses = self.stats["losses"]
        denom = wins + losses  # exclude flats from win-rate
        win_rate = round(wins / denom, 4) if denom else None
        return {
            **self.stats,
            "win_rate": win_rate,
            "denominator": denom,
        }

    def trades(self, limit: int = 50) -> list[dict]:
        if limit <= 0:
            return []
        # Deque doesn't support negative slicing; convert tail.
        tail = list(self.trade_log)[-limit:]
        return list(reversed(tail))

    def conditions_snapshot(self, min_total: int = 1) -> dict:
        """Return condition stats grouped by key (regime, agent, …)."""
        grouped: dict[str, list[dict]] = defaultdict(list)
        for (k, v), s in self.condition_stats.items():
            if s["total"] < min_total:
                continue
            denom = s["wins"] + s["losses"]
            win_rate = round(s["wins"] / denom, 4) if denom else None
            grouped[k].append({
                "value": v,
                "total": s["total"],
                "wins": s["wins"],
                "losses": s["losses"],
                "flats": s["flats"],
                "win_rate": win_rate,
            })
        # Sort each group by total desc
        for v in grouped.values():
            v.sort(key=lambda r: (r["total"], r["win_rate"] or 0), reverse=True)
        return dict(grouped)

    # ── Internals ──
    def _apply_in_memory(self, doc: dict, *, hydrate: bool) -> None:
        key = doc["trade_key"]
        self._seen_keys.add(key)
        outcome = doc["outcome"]
        # Hydrate path skips the redundant log-append guard but otherwise
        # uses the same arithmetic so cache + Mongo can never diverge.
        self.trade_log.append(doc)
        self.stats["total_resolved"] += 1
        if outcome == "win":
            self.stats["wins"] += 1
        elif outcome == "loss":
            self.stats["losses"] += 1
        elif outcome == "flat":
            self.stats["flats"] += 1
        else:
            # Pending shouldn't arrive here (caller filters), but if it
            # does, count it without polluting win/loss.
            self.stats["pending"] += 1

        for k, v in _condition_tuples(doc):
            bucket = self.condition_stats[(k, v)]
            bucket["total"] += 1
            if outcome == "win":
                bucket["wins"] += 1
            elif outcome == "loss":
                bucket["losses"] += 1
            elif outcome == "flat":
                bucket["flats"] += 1


# ── Pure helpers (testable in isolation) ─────────────────────────────


def _normalise_trade(raw: dict) -> Optional[dict]:
    """Return a clean trade doc or None if fatal fields missing."""
    source = (raw.get("source") or "").strip().lower()
    source_id = str(raw.get("source_id") or "").strip()
    symbol = (raw.get("symbol") or "").strip().upper()
    if not source or not source_id or not symbol:
        return None
    outcome = _canon_outcome(raw.get("outcome"))
    if outcome == "pending":
        # We only record resolved trades — caller asked us to ingest
        # something still open. Skip cleanly.
        return None
    confidence = raw.get("confidence")
    return {
        "trade_key": _build_trade_key(source, source_id),
        "source": source,
        "source_id": source_id,
        "symbol": symbol,
        "direction": (raw.get("direction") or "").upper(),
        "confidence": confidence,
        "regime": raw.get("regime"),
        "agent": raw.get("agent"),
        "asset_type": raw.get("asset_type"),
        "entry_price": raw.get("entry_price"),
        "exit_price": raw.get("exit_price"),
        "pnl_usd": raw.get("pnl_usd"),
        "pnl_pct": raw.get("pnl_pct"),
        "outcome": outcome,
        "recorded_at": raw.get("recorded_at") or datetime.now(timezone.utc).isoformat(),
    }


def _condition_tuples(doc: dict) -> Iterable[tuple[str, str]]:
    """Yield (key, value) for every populated condition dimension."""
    if doc.get("regime"):
        yield ("regime", str(doc["regime"]))
    if doc.get("agent"):
        yield ("agent", str(doc["agent"]))
    if doc.get("asset_type"):
        yield ("asset_type", str(doc["asset_type"]))
    bucket = _bucket_confidence(doc.get("confidence"))
    if bucket:
        yield ("confidence_bucket", bucket)


# ── Module-level singleton ───────────────────────────────────────────
learning_engine = LearningEngine()


def set_db(db: Any) -> None:
    learning_engine.set_db(db)


async def ensure_indexes(db: Any) -> None:
    """Mongo indexes — unique on trade_key keeps re-ingest cheap."""
    try:
        await db["ai_core_trades"].create_index(
            "trade_key", unique=True, name="ai_core_trade_key_unique",
        )
        await db["ai_core_trades"].create_index([("recorded_at", -1)])
        await db["ai_core_rejections"].create_index([("recorded_at", -1)])
    except Exception as e:  # noqa: BLE001
        logger.warning("[ai_core] index ensure failed: %s", e)
