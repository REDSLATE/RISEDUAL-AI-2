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
_DEFAULT_BUCKETS_5BIN: list[tuple[str, float, float]] = [
    ("0-60", 0.0, 60.0),
    ("60-70", 60.0, 70.0),
    ("70-80", 70.0, 80.0),
    ("80-90", 80.0, 90.0),
    ("90-100", 90.0, 100.01),
]

# Candidate v2: finer at the high-confidence end where toxic spikes live.
_CANDIDATE_V2_BUCKETS_6BIN: list[tuple[str, float, float]] = [
    ("0-60", 0.0, 60.0),
    ("60-70", 60.0, 70.0),
    ("70-80", 70.0, 80.0),
    ("80-85", 80.0, 85.0),
    ("85-90", 85.0, 90.0),
    ("90-100", 90.0, 100.01),
]

# Backward-compat alias used by helpers + existing tests.
_CONF_BUCKETS = _DEFAULT_BUCKETS_5BIN

# Outcome canonicalisation — accepts the various shapes upstream
# services actually emit (paper_trades' "win"/"loss"/"flat";
# predictions' boolean `correct`; raw "hit"/"miss" strings).
_WIN_TOKENS = {"win", "hit", "correct", True, "true", 1, "1"}
_LOSS_TOKENS = {"loss", "miss", False, "false", 0, "0"}
_FLAT_TOKENS = {"flat", "neutral", "tie", None}


def _bucket_confidence(
    conf_pct: Optional[float],
    buckets: Optional[list] = None,
) -> Optional[str]:
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
    for label, lo, hi in (buckets if buckets is not None else _CONF_BUCKETS):
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
    """Schema-aware learning engine. Each engine tracks its own
    rolling stats + condition aggregates over the same source firehose.

    Constructor args
    ----------------
    name : str
        Identifier (e.g. "live", "candidate_v2"). Determines the
        Mongo collection used for persistence: legacy "live" maps to
        ``ai_core_trades`` for back-compat; everything else maps to
        ``ai_core_engine_{name}_trades``.
    schema : SchemaConfig | None
        Bucketing + dimension extraction rules. Default = the v1
        five-bin live schema.

    All public methods are coroutine-safe; the in-memory dicts are
    mutated only from the asyncio event loop, so no lock is required.
    """

    def __init__(
        self,
        name: str = "live",
        schema: Optional["SchemaConfig"] = None,
    ) -> None:
        self.name = name
        self.schema = schema or _LIVE_SCHEMA
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

    @property
    def trades_collection(self) -> str:
        """Legacy 'live' uses ai_core_trades for back-compat. Other
        engines get their own namespaced collection so they can never
        accidentally overwrite each other."""
        if self.name == "live":
            return "ai_core_trades"
        return f"ai_core_engine_{self.name}_trades"

    # ── Cold-start hydration ──
    async def hydrate(self) -> None:
        """Replay the last MAX_LOG resolved trades from Mongo into
        the in-memory log + stats so a backend restart doesn't lose
        rolling counts. Idempotent — guard flag prevents double-fill."""
        if self._hydrated or self._db is None:
            return
        self._hydrated = True
        try:
            cursor = self._db[self.trades_collection].find(
                {}, {"_id": 0}
            ).sort("recorded_at", -1).limit(MAX_LOG)
            docs = await cursor.to_list(length=MAX_LOG)
            # Reverse so oldest first; trade_log is naturally chronological.
            for doc in reversed(docs):
                self._apply_in_memory(doc, hydrate=True)
            logger.info(
                "[ai_core:%s] hydrated %d resolved trades from Mongo",
                self.name, len(docs),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[ai_core:%s] hydrate failed: %s", self.name, e)

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
                await self._db[self.trades_collection].update_one(
                    {"trade_key": key},
                    {"$setOnInsert": normalised},
                    upsert=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[ai_core:%s] trade upsert failed for %s: %s", self.name, key, e)

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
        """Wipe all in-memory + Mongo state. Dev/demo only.

        Per-engine: only this engine's collection is wiped. The
        ``ai_core_alerts`` collection is shared and only cleared by the
        live engine to preserve alert history when a candidate is
        being tinkered with."""
        self.trade_log.clear()
        self._seen_keys.clear()
        for k in list(self.stats.keys()):
            self.stats[k] = 0
        self.condition_stats.clear()
        self._hydrated = False
        if self._db is not None:
            try:
                await self._db[self.trades_collection].delete_many({})
                if self.name == "live":
                    await self._db["ai_core_rejections"].delete_many({})
                    await self._db["ai_core_alerts"].delete_many({})
            except Exception as e:  # noqa: BLE001
                logger.warning("[ai_core:%s] reset wipe failed: %s", self.name, e)
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

        for k, v in self.schema.extract_conditions(doc):
            bucket = self.condition_stats[(k, v)]
            bucket["total"] += 1
            if outcome == "win":
                bucket["wins"] += 1
            elif outcome == "loss":
                bucket["losses"] += 1
            elif outcome == "flat":
                bucket["flats"] += 1


def _direction_family(direction: Any) -> Optional[str]:
    """Map any direction token to BULLISH/BEARISH/NEUTRAL.

    Routes through ``services.prediction_tracker.canonical_ai_dir``
    so the LONG/SHORT/UNKNOWN classification is consistent across
    every service. Pre-2026-05-01 the local sets here drifted out
    of sync with prediction_tracker's, which was one leg of the
    direction-token bug class.
    """
    from services.prediction_tracker import canonical_ai_dir, DIRECTION_NEUTRAL
    canon = canonical_ai_dir(direction)
    if canon == "LONG":
        return "BULLISH"
    if canon == "SHORT":
        return "BEARISH"
    if str(direction or "").upper() in DIRECTION_NEUTRAL:
        return "NEUTRAL"
    return None


# ── Schema definitions ───────────────────────────────────────────────


class SchemaConfig:
    """A learning-engine schema. Pure-data: no I/O.

    Two extension points:
    * ``confidence_buckets`` controls granularity of the confidence
      dimension.
    * ``extract_conditions`` controls which (key, value) tuples are
      emitted per resolved trade — i.e. how the engine slices wins.

    Schemas are intentionally simple to keep candidate engines
    auditable; complex feature engineering belongs in the live
    decision-time stack, not in the post-resolution analytics.
    """

    def __init__(
        self,
        name: str,
        confidence_buckets: list[tuple[str, float, float]],
        dimensions: list[str],
    ) -> None:
        self.name = name
        self.confidence_buckets = confidence_buckets
        self.dimensions = dimensions  # ordered, used for grep audits

    def extract_conditions(self, doc: dict) -> Iterable[tuple[str, str]]:
        """Yield (dim_key, dim_value) pairs for a resolved trade."""
        if "regime" in self.dimensions and doc.get("regime"):
            yield ("regime", str(doc["regime"]))
        if "agent" in self.dimensions and doc.get("agent"):
            yield ("agent", str(doc["agent"]))
        if "asset_type" in self.dimensions and doc.get("asset_type"):
            yield ("asset_type", str(doc["asset_type"]))
        if "confidence_bucket" in self.dimensions:
            bucket = _bucket_confidence(
                doc.get("confidence"), self.confidence_buckets,
            )
            if bucket:
                yield ("confidence_bucket", bucket)
        # ── candidate-only dimensions ──
        if "direction_family" in self.dimensions:
            fam = _direction_family(doc.get("direction"))
            if fam:
                yield ("direction_family", fam)
        if "confidence_x_agent" in self.dimensions:
            agent = doc.get("agent")
            bucket = _bucket_confidence(
                doc.get("confidence"), self.confidence_buckets,
            )
            if agent and bucket:
                yield ("confidence_x_agent", f"{agent}@{bucket}")


_LIVE_SCHEMA = SchemaConfig(
    name="live_v1",
    confidence_buckets=_DEFAULT_BUCKETS_5BIN,
    dimensions=["regime", "agent", "asset_type", "confidence_bucket"],
)

_CANDIDATE_V2_SCHEMA = SchemaConfig(
    name="candidate_v2",
    confidence_buckets=_CANDIDATE_V2_BUCKETS_6BIN,
    dimensions=[
        "regime", "agent", "asset_type", "confidence_bucket",
        "direction_family", "confidence_x_agent",
    ],
)


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


# ── Module-level singleton + registry ───────────────────────────────


class LearningEngineRegistry:
    """Holds all engines. Every ``record_trade`` call fans out to every
    registered engine — they observe the same firehose with their own
    schemas. Exactly one engine is tagged ``live``; promotion swaps
    that tag.

    Promotion is **purely a label flip in this registry** — it does
    NOT cross the dual-stack firewall (Council still reads
    prediction-tracker stats, not AI Core). Promoting a candidate to
    live affects only which engine the public dashboard reports as
    "the official scoreboard"."""

    def __init__(self) -> None:
        self._engines: dict[str, LearningEngine] = {}
        self._live_name: str = "live"

    def register(self, engine: LearningEngine, *, set_live: bool = False) -> None:
        self._engines[engine.name] = engine
        if set_live:
            self._live_name = engine.name

    def get(self, name: str) -> Optional[LearningEngine]:
        return self._engines.get(name)

    def all(self) -> list[LearningEngine]:
        return list(self._engines.values())

    def names(self) -> list[str]:
        return list(self._engines.keys())

    @property
    def live_name(self) -> str:
        return self._live_name

    @property
    def live(self) -> LearningEngine:
        return self._engines[self._live_name]

    async def broadcast_trade(self, raw: dict) -> dict:
        """Fan-out: send the trade to every engine. Returns a dict
        ``{engine_name: result}``. The live engine's result is
        bubbled up as the canonical response for back-compat."""
        results: dict[str, dict] = {}
        for name, engine in self._engines.items():
            try:
                results[name] = await engine.record_trade(raw)
            except Exception as e:  # noqa: BLE001
                logger.warning("[registry] engine %s record_trade failed: %s", name, e)
                results[name] = {"ok": False, "reason": str(e)}
        return {
            "live_result": results.get(self._live_name, {"ok": False}),
            "engines": results,
        }

    async def broadcast_rejection(self, raw: dict) -> dict:
        return await self.live.record_rejection(raw)

    async def reset_all(self) -> dict:
        out = {}
        for name, engine in self._engines.items():
            out[name] = await engine.reset()
        return {"ok": True, "engines": out}

    def set_db_for_all(self, db: Any) -> None:
        for engine in self._engines.values():
            engine.set_db(db)

    def promote(self, name: str) -> dict:
        """Flip the ``live`` tag to ``name``. The previous live
        becomes a candidate — never retired. Idempotent."""
        if name not in self._engines:
            return {"ok": False, "reason": "unknown_engine"}
        prev = self._live_name
        if prev == name:
            return {"ok": True, "noop": True, "live": name}
        self._live_name = name
        logger.info("[registry] promoted %s → live (was: %s)", name, prev)
        return {"ok": True, "live": name, "demoted": prev}


# Registry + default engines. The legacy ``learning_engine`` symbol
# is kept as an alias for callers that imported it pre-registry.
registry = LearningEngineRegistry()
registry.register(LearningEngine(name="live", schema=_LIVE_SCHEMA), set_live=True)
registry.register(LearningEngine(name="candidate_v2", schema=_CANDIDATE_V2_SCHEMA))

# Alias so old callers (routes/ai_core_routes.py, ai_core_autowire.py,
# server.py:_run_ai_core_nightly) keep working with no edits required.
learning_engine = registry.live


def set_db(db: Any) -> None:
    registry.set_db_for_all(db)


async def ensure_indexes(db: Any) -> None:
    """Mongo indexes — unique on trade_key keeps re-ingest cheap.
    Each engine's collection gets the same index treatment.

    Self-healing: if a legacy hardcoded-named index is found on the
    same key (e.g. ``ai_core_trade_key_unique`` from a pre-rename
    deployment), drop it before re-creating with the f-string name.
    Avoids the recurring ``IndexOptionsConflict`` (MongoDB error 85)
    we used to log every restart.
    """
    try:
        for engine in registry.all():
            coll = engine.trades_collection
            target_name = f"{coll}_trade_key_unique"
            try:
                existing = await db[coll].index_information()
            except Exception:  # noqa: BLE001
                existing = {}
            for name, info in existing.items():
                if name == target_name:
                    continue
                # Same key, different name → legacy. Drop so the
                # create_index below can install the canonical one.
                if list(info.get("key", [])) == [("trade_key", 1)]:
                    try:
                        await db[coll].drop_index(name)
                        logger.info(
                            "[ai_core] dropped legacy index %r on %s — "
                            "will recreate as %r", name, coll, target_name,
                        )
                    except Exception as drop_err:  # noqa: BLE001
                        logger.warning(
                            "[ai_core] could not drop legacy index %r on %s: %s",
                            name, coll, drop_err,
                        )
            await db[coll].create_index(
                "trade_key", unique=True, name=target_name,
            )
            await db[coll].create_index([("recorded_at", -1)])
        await db["ai_core_rejections"].create_index([("recorded_at", -1)])
    except Exception as e:  # noqa: BLE001
        logger.warning("[ai_core] index ensure failed: %s", e)
