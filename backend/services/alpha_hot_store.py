"""Alpha Day Trader — local append-only hot store.

Why this exists
---------------
Mongo is expensive and finite. Every scan tick would write per-symbol
observation rows, and PUMP being scanned 227× would balloon the
``alpha_setup_observations`` collection with duplicate telemetry.

Rule: **raw high-frequency events live here, only rollups + resolved
outcomes go to Mongo**.

Schema
------
* ``lifecycle_events`` — every state transition and every gate observation:
    (id, setup_id, symbol, event, stage, payload_json, ts_ns)
* ``latency_events`` — per-setup latency samples, keyed by phase
* ``exec_dedup`` — cross-scanner symbol lock at the execution boundary
  (short-lived, ``expires_at`` enforced by callers)

Everything is in a single WAL-mode SQLite file living under
``/app/backend/data/alpha_hot_store.sqlite`` — survives process
restarts, does not touch Mongo. Retention is done by ``prune()`` which
callers invoke on a schedule.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "/app/backend/data/alpha_hot_store.sqlite"
_LOCK = threading.Lock()
_DB_PATH: Optional[str] = None


def _path() -> str:
    return _DB_PATH or os.environ.get("ALPHA_HOT_STORE_PATH") or _DEFAULT_PATH


def _ensure_dir() -> None:
    p = _path()
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)


def init(path: Optional[str] = None) -> None:
    """Idempotently create the DB file + schema. Safe to call on boot."""
    global _DB_PATH
    if path:
        _DB_PATH = path
    _ensure_dir()
    with _connect() as con:
        con.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS lifecycle_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                setup_id    TEXT NOT NULL,
                symbol      TEXT,
                event       TEXT NOT NULL,
                stage       TEXT,
                payload     TEXT,
                ts_ns       INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_life_setup ON lifecycle_events(setup_id);
            CREATE INDEX IF NOT EXISTS ix_life_ts    ON lifecycle_events(ts_ns);
            CREATE INDEX IF NOT EXISTS ix_life_event ON lifecycle_events(event);

            CREATE TABLE IF NOT EXISTS latency_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                setup_id    TEXT NOT NULL,
                phase       TEXT NOT NULL,       -- signal_to_trigger, trigger_to_intent, intent_to_broker, broker_to_fill
                duration_ms INTEGER NOT NULL,
                ts_ns       INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_lat_setup ON latency_events(setup_id);
            CREATE INDEX IF NOT EXISTS ix_lat_phase ON latency_events(phase);

            CREATE TABLE IF NOT EXISTS exec_dedup (
                symbol      TEXT NOT NULL,
                setup_id    TEXT NOT NULL,
                source      TEXT NOT NULL,       -- 'alpha_daytrader' | 'day_trade_scanner'
                acquired_at INTEGER NOT NULL,    -- ns
                expires_at  INTEGER NOT NULL,    -- ns
                PRIMARY KEY (symbol)
            );
            """
        )


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    _ensure_dir()
    con = sqlite3.connect(_path(), timeout=5.0, isolation_level=None)
    try:
        con.row_factory = sqlite3.Row
        yield con
    finally:
        con.close()


def _now_ns() -> int:
    return time.time_ns()


# ─── writes ──────────────────────────────────────────────────────


def record_event(
    setup_id: str,
    event: str,
    *,
    stage: str = "",
    symbol: str = "",
    payload: Optional[dict] = None,
) -> None:
    """Append a lifecycle event. Never raises — the caller must not
    depend on hot-store success for correctness."""
    try:
        with _LOCK, _connect() as con:
            con.execute(
                "INSERT INTO lifecycle_events(setup_id, symbol, event, stage, payload, ts_ns) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (setup_id, symbol.upper() if symbol else "",
                 event, stage,
                 json.dumps(payload or {}, default=str), _now_ns()),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_hot_store] record_event failed: %s", exc)


def record_latency(setup_id: str, phase: str, duration_ms: int) -> None:
    try:
        with _LOCK, _connect() as con:
            con.execute(
                "INSERT INTO latency_events(setup_id, phase, duration_ms, ts_ns) VALUES (?, ?, ?, ?)",
                (setup_id, phase, int(duration_ms), _now_ns()),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_hot_store] record_latency failed: %s", exc)


# ─── cross-scanner dedup ─────────────────────────────────────────


def try_acquire_symbol_lock(
    symbol: str, *, setup_id: str, source: str, ttl_seconds: int = 90,
) -> bool:
    """Best-effort mutex at the execution boundary.

    Returns True when the caller wins the lock (and can proceed to
    submit the trade). Returns False when another source (or the same
    source with a different setup_id) already holds a non-expired
    lock on ``symbol``.

    This prevents the old ``day_trade_scanner`` and the new Alpha Day
    Trader from double-buying the same underlying move.
    """
    now = _now_ns()
    expires = now + int(ttl_seconds * 1e9)
    sym = symbol.upper()
    try:
        with _LOCK, _connect() as con:
            row = con.execute(
                "SELECT setup_id, source, expires_at FROM exec_dedup WHERE symbol=?",
                (sym,),
            ).fetchone()
            if row and int(row["expires_at"]) > now:
                # Already held. Only re-acquire if same setup_id (idempotent).
                if row["setup_id"] == setup_id and row["source"] == source:
                    con.execute(
                        "UPDATE exec_dedup SET expires_at=? WHERE symbol=?",
                        (expires, sym),
                    )
                    return True
                return False
            con.execute(
                "INSERT OR REPLACE INTO exec_dedup(symbol, setup_id, source, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sym, setup_id, source, now, expires),
            )
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_hot_store] lock acquire failed: %s", exc)
        return True  # fail-open: never block a legit trade on lock-service failure


def release_symbol_lock(symbol: str, *, setup_id: str) -> None:
    try:
        with _LOCK, _connect() as con:
            con.execute(
                "DELETE FROM exec_dedup WHERE symbol=? AND setup_id=?",
                (symbol.upper(), setup_id),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_hot_store] lock release failed: %s", exc)


# ─── reads (for rollup + admin) ──────────────────────────────────


def events_for_setup(setup_id: str) -> list[dict]:
    try:
        with _connect() as con:
            rows = con.execute(
                "SELECT event, stage, symbol, payload, ts_ns FROM lifecycle_events "
                "WHERE setup_id=? ORDER BY ts_ns ASC",
                (setup_id,),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            try:
                payload = json.loads(r["payload"] or "{}")
            except Exception:  # noqa: BLE001
                payload = {}
            out.append({
                "event": r["event"],
                "stage": r["stage"],
                "symbol": r["symbol"],
                "payload": payload,
                "ts": datetime.fromtimestamp(r["ts_ns"] / 1e9, tz=timezone.utc).isoformat(),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_hot_store] events_for_setup failed: %s", exc)
        return []


def latency_samples(setup_id: str) -> dict[str, list[int]]:
    try:
        with _connect() as con:
            rows = con.execute(
                "SELECT phase, duration_ms FROM latency_events WHERE setup_id=?",
                (setup_id,),
            ).fetchall()
        out: dict[str, list[int]] = {}
        for r in rows:
            out.setdefault(r["phase"], []).append(int(r["duration_ms"]))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_hot_store] latency_samples failed: %s", exc)
        return {}


def counts_since(since_ns: int) -> dict:
    """Return coarse counters for the admin UI without hitting Mongo."""
    try:
        with _connect() as con:
            events = con.execute(
                "SELECT event, COUNT(*) AS n FROM lifecycle_events WHERE ts_ns>=? GROUP BY event",
                (since_ns,),
            ).fetchall()
        return {r["event"]: int(r["n"]) for r in events}
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_hot_store] counts_since failed: %s", exc)
        return {}


# ─── retention ───────────────────────────────────────────────────


def prune(*, older_than_days: int = 14) -> int:
    """Delete lifecycle/latency rows older than ``older_than_days``.
    Returns number of rows deleted. Callers should schedule this daily.
    """
    cutoff = _now_ns() - int(older_than_days * 86_400 * 1e9)
    total = 0
    try:
        with _LOCK, _connect() as con:
            for table in ("lifecycle_events", "latency_events"):
                cur = con.execute(f"DELETE FROM {table} WHERE ts_ns < ?", (cutoff,))
                total += cur.rowcount or 0
            # Expired locks
            con.execute("DELETE FROM exec_dedup WHERE expires_at < ?", (_now_ns(),))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[alpha_hot_store] prune failed: %s", exc)
    return total


__all__ = [
    "init",
    "record_event",
    "record_latency",
    "try_acquire_symbol_lock",
    "release_symbol_lock",
    "events_for_setup",
    "latency_samples",
    "counts_since",
    "prune",
]
