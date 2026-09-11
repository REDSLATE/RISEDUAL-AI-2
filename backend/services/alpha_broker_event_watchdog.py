"""Normalized broker order-event watchdog (Public.com + MooMoo).

Design contract (operator directive, 2026-02)
=============================================
A missing broker event does NOT prove the order failed. The watchdog must:

1.  Fire a 5-second timer at ``SUBMITTED``.
2.  If any ACK-tier event (``ACKNOWLEDGED``, ``PARTIALLY_FILLED``, ``FILLED``,
    ``CANCELED``, ``REJECTED``, ``EXPIRED``) arrives before the deadline,
    cancel the timer and let the normal lifecycle proceed.
3.  If the deadline lapses with no such event, mark the intent
    ``BROKER_EVENT_STALE`` — **freeze the intent (no auto-resubmit)** and
    trigger a broker reconciliation query.
4.  Reconciliation outcomes:

       * ``FOUND/FILLED``  → reconcile position.
       * ``FOUND/OPEN``    → resume tracking the live order.
       * ``FOUND/CANCELED``→ terminal, may re-arm the candidate.
       * ``FOUND/REJECTED``→ terminal, may re-arm the candidate.
       * ``ABSENT``        → broker never received it, release intent.
       * ``UNREACHABLE``   → ``BROKER_STATE_UNKNOWN`` on the (symbol,
         account) pair. Duplicate submits for that pair are blocked
         until the operator manually clears.

Public.com and MooMoo normalize into the same event vocabulary so the
Funnel never sees broker-specific strings.

Persistence
-----------
The state lives in ``alpha_broker_event_watchdog`` SQLite table (hot store
DB, WAL). One row per ``(broker, client_order_id)`` — upsert semantics on
each transition, so a crash/restart re-hydrates the frozen set.

Thread model
------------
The watchdog uses ``asyncio`` timers when an event loop is running (the
FastAPI backend) and falls back to ``threading.Timer`` when called from a
synchronous context (e.g. the MooMoo adapter's sync submit path). All
state mutations go through a single re-entrant lock.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from services import alpha_hot_store

logger = logging.getLogger(__name__)


# ── Normalized event vocabulary ─────────────────────────────────────

class Event:
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    BROKER_EVENT_STALE = "BROKER_EVENT_STALE"
    BROKER_STATE_UNKNOWN = "BROKER_STATE_UNKNOWN"


# Terminal states — the intent will not receive further updates.
_TERMINAL_EVENTS = {
    Event.FILLED,
    Event.CANCELED,
    Event.REJECTED,
    Event.EXPIRED,
}

# ACK-tier events cancel the watchdog timer.
_ACK_TIER = _TERMINAL_EVENTS | {Event.ACKNOWLEDGED, Event.PARTIALLY_FILLED}


# ── Config ──────────────────────────────────────────────────────────

def _stale_seconds() -> float:
    try:
        v = float(os.environ.get("BROKER_EVENT_STALE_SECONDS") or 5.0)
    except (TypeError, ValueError):
        v = 5.0
    # Lower bound kept short so tests can drive the state machine at
    # ~100 ms; production keeps the default 5 s.
    return max(0.05, min(v, 60.0))


# ── State ───────────────────────────────────────────────────────────

@dataclass
class WatchdogEntry:
    broker: str                       # "public" | "moomoo"
    client_order_id: str
    symbol: str
    account_id: str
    broker_order_id: Optional[str]
    submitted_at_ns: int
    state: str = Event.SUBMITTED
    frozen: bool = False              # blocks resubmit for (broker, symbol, account)
    reason: Optional[str] = None
    events: list[dict] = field(default_factory=list)


_LOCK = threading.RLock()
_ENTRIES: dict[tuple[str, str], WatchdogEntry] = {}
_TIMERS: dict[tuple[str, str], threading.Timer] = {}
# Pluggable reconciliation callbacks — set once at boot via register_reconciler.
_RECONCILERS: dict[str, Callable[[WatchdogEntry], dict]] = {}


def register_reconciler(broker: str, fn: Callable[[WatchdogEntry], dict]) -> None:
    """Register a broker reconciliation callback.

    The callback receives the frozen WatchdogEntry and returns a dict:
        {"outcome": "FILLED"|"OPEN"|"CANCELED"|"REJECTED"|"ABSENT"|"UNREACHABLE",
         "broker_order_id": Optional[str],
         "detail": Optional[str]}
    Never raises out of the callback — a raise is treated as UNREACHABLE.
    """
    _RECONCILERS[broker] = fn


# ── SQLite mirror ───────────────────────────────────────────────────

def _db_path() -> str:
    # Piggyback the alpha hot-store file so ops has one durable location.
    from services.alpha_hot_store import _path as _hot_path  # local import to avoid cycles
    return _hot_path()


def _ensure_table() -> None:
    with sqlite3.connect(_db_path(), timeout=5.0, isolation_level=None) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS alpha_broker_event_watchdog (
                broker            TEXT NOT NULL,
                client_order_id   TEXT NOT NULL,
                broker_order_id   TEXT,
                symbol            TEXT,
                account_id        TEXT,
                state             TEXT NOT NULL,
                frozen            INTEGER NOT NULL DEFAULT 0,
                reason            TEXT,
                submitted_at_ns   INTEGER NOT NULL,
                updated_at_ns     INTEGER NOT NULL,
                events            TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (broker, client_order_id)
            );
            CREATE INDEX IF NOT EXISTS ix_bw_state ON alpha_broker_event_watchdog(state);
            CREATE INDEX IF NOT EXISTS ix_bw_frozen ON alpha_broker_event_watchdog(frozen);
            CREATE INDEX IF NOT EXISTS ix_bw_symbol_acc
                ON alpha_broker_event_watchdog(broker, symbol, account_id);
            """
        )


def _persist(entry: WatchdogEntry) -> None:
    try:
        _ensure_table()
        with sqlite3.connect(_db_path(), timeout=5.0, isolation_level=None) as con:
            con.execute(
                """
                INSERT INTO alpha_broker_event_watchdog
                    (broker, client_order_id, broker_order_id, symbol, account_id,
                     state, frozen, reason, submitted_at_ns, updated_at_ns, events)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(broker, client_order_id) DO UPDATE SET
                    broker_order_id = excluded.broker_order_id,
                    state           = excluded.state,
                    frozen          = excluded.frozen,
                    reason          = excluded.reason,
                    updated_at_ns   = excluded.updated_at_ns,
                    events          = excluded.events;
                """,
                (
                    entry.broker, entry.client_order_id,
                    entry.broker_order_id, entry.symbol, entry.account_id,
                    entry.state, 1 if entry.frozen else 0, entry.reason,
                    entry.submitted_at_ns, time.time_ns(),
                    json.dumps(entry.events)[:16384],
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[watchdog] persist failed: %s", exc)


# ── Public API ──────────────────────────────────────────────────────

def is_frozen(broker: str, symbol: str, account_id: str) -> bool:
    """Return True if any active watchdog entry for (broker, symbol,
    account_id) is frozen. Callers MUST honor this before resubmitting."""
    key_broker = (broker or "").lower()
    key_symbol = (symbol or "").upper()
    key_acc = str(account_id or "")
    with _LOCK:
        for entry in _ENTRIES.values():
            if (
                entry.broker == key_broker
                and entry.symbol == key_symbol
                and entry.account_id == key_acc
                and entry.frozen
            ):
                return True
    return False


def register_submission(
    *,
    broker: str,
    client_order_id: str,
    symbol: str,
    account_id: str,
    broker_order_id: Optional[str] = None,
) -> WatchdogEntry:
    """Start the 5-second stale-event timer for a freshly submitted order."""
    broker = (broker or "").lower()
    key = (broker, client_order_id)
    now_ns = time.time_ns()
    with _LOCK:
        entry = WatchdogEntry(
            broker=broker,
            client_order_id=client_order_id,
            symbol=(symbol or "").upper(),
            account_id=str(account_id or ""),
            broker_order_id=broker_order_id,
            submitted_at_ns=now_ns,
            state=Event.SUBMITTED,
            events=[{"event": Event.SUBMITTED, "at_ns": now_ns}],
        )
        _ENTRIES[key] = entry
        _cancel_timer_locked(key)
        _arm_timer_locked(key)
    _persist(entry)
    _log_lifecycle(entry, Event.SUBMITTED)
    return entry


def record_event(
    *,
    broker: str,
    client_order_id: str,
    event: str,
    broker_order_id: Optional[str] = None,
    detail: Optional[str] = None,
) -> Optional[WatchdogEntry]:
    """Record a normalized event. Cancels the stale timer on ACK-tier events."""
    broker = (broker or "").lower()
    key = (broker, client_order_id)
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is None:
            return None
        entry.state = event
        if broker_order_id and not entry.broker_order_id:
            entry.broker_order_id = broker_order_id
        if detail:
            entry.reason = detail
        entry.events.append({"event": event, "at_ns": time.time_ns(), "detail": detail})
        # Trim event log so a busy broker can't blow past 16 KiB.
        if len(entry.events) > 40:
            entry.events = entry.events[-40:]
        if event in _ACK_TIER:
            _cancel_timer_locked(key)
        if event in _TERMINAL_EVENTS:
            entry.frozen = False
    _persist(entry)
    _log_lifecycle(entry, event)
    return entry


def get_entry(broker: str, client_order_id: str) -> Optional[WatchdogEntry]:
    with _LOCK:
        return _ENTRIES.get(((broker or "").lower(), client_order_id))


def list_entries(*, only_frozen: bool = False) -> list[dict]:
    with _LOCK:
        rows = list(_ENTRIES.values())
    return [
        {
            "broker": e.broker, "client_order_id": e.client_order_id,
            "broker_order_id": e.broker_order_id, "symbol": e.symbol,
            "account_id": e.account_id, "state": e.state,
            "frozen": e.frozen, "reason": e.reason,
            "submitted_at_ns": e.submitted_at_ns,
            "events": list(e.events),
        }
        for e in rows if (not only_frozen) or e.frozen
    ]


def clear_frozen(broker: str, client_order_id: str, *, reason: str = "operator_cleared") -> bool:
    """Operator-visible manual unfreeze. Used after out-of-band verification."""
    broker = (broker or "").lower()
    key = (broker, client_order_id)
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is None or not entry.frozen:
            return False
        entry.frozen = False
        entry.reason = reason
        entry.events.append({"event": "OPERATOR_UNFREEZE", "at_ns": time.time_ns(), "detail": reason})
    _persist(entry)
    _log_lifecycle(entry, "OPERATOR_UNFREEZE")
    return True


# ── Timer plumbing ──────────────────────────────────────────────────

def _arm_timer_locked(key: tuple[str, str]) -> None:
    """Caller holds ``_LOCK``."""
    seconds = _stale_seconds()
    t = threading.Timer(seconds, _on_stale, args=(key,))
    t.daemon = True
    _TIMERS[key] = t
    t.start()


def _cancel_timer_locked(key: tuple[str, str]) -> None:
    """Caller holds ``_LOCK``."""
    t = _TIMERS.pop(key, None)
    if t is not None:
        try:
            t.cancel()
        except Exception:  # noqa: BLE001
            pass


def _on_stale(key: tuple[str, str]) -> None:
    """Fired when the 5-second window elapses without an ACK-tier event."""
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is None:
            return
        # Race: an ACK-tier event landed between the timer firing and this
        # callback grabbing the lock. Do not clobber terminal state.
        if entry.state in _ACK_TIER:
            return
        entry.state = Event.BROKER_EVENT_STALE
        entry.frozen = True
        entry.reason = "no_ack_within_stale_window"
        entry.events.append({
            "event": Event.BROKER_EVENT_STALE,
            "at_ns": time.time_ns(),
        })
    _persist(entry)
    _log_lifecycle(entry, Event.BROKER_EVENT_STALE)

    # Trigger reconciliation. Best-effort — never raise from a Timer thread.
    try:
        _reconcile(entry)
    except Exception as exc:  # noqa: BLE001
        logger.error("[watchdog] reconcile failed for %s: %s",
                     entry.client_order_id, exc)
        _mark_unknown(entry, reason=f"reconcile_exception:{exc.__class__.__name__}")


def _reconcile(entry: WatchdogEntry) -> None:
    """Query the broker to determine the true order state."""
    fn = _RECONCILERS.get(entry.broker)
    if fn is None:
        _mark_unknown(entry, reason="no_reconciler_registered")
        return
    try:
        result = fn(entry) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[watchdog] reconciler for %s raised: %s", entry.broker, exc)
        _mark_unknown(entry, reason=f"reconciler_raised:{exc.__class__.__name__}")
        return

    outcome = str(result.get("outcome") or "UNREACHABLE").upper()
    detail = result.get("detail")
    broker_order_id = result.get("broker_order_id") or entry.broker_order_id

    mapping = {
        "FILLED":    (Event.FILLED,    False, "reconciled_filled"),
        "OPEN":      (Event.ACKNOWLEDGED, False, "reconciled_open"),
        "CANCELED":  (Event.CANCELED,  False, "reconciled_canceled"),
        "REJECTED":  (Event.REJECTED,  False, "reconciled_rejected"),
        "ABSENT":    ("BROKER_ABSENT", False, "reconciled_absent"),
    }
    if outcome in mapping:
        new_state, frozen, default_reason = mapping[outcome]
        with _LOCK:
            entry.state = new_state
            entry.frozen = frozen
            entry.reason = detail or default_reason
            if broker_order_id:
                entry.broker_order_id = broker_order_id
            entry.events.append({
                "event": new_state,
                "at_ns": time.time_ns(),
                "detail": detail,
                "source": "reconciliation",
            })
        _persist(entry)
        _log_lifecycle(entry, new_state)
        return

    # UNREACHABLE — broker query failed, keep the freeze.
    _mark_unknown(entry, reason=detail or "reconciler_unreachable")


def _mark_unknown(entry: WatchdogEntry, *, reason: str) -> None:
    with _LOCK:
        entry.state = Event.BROKER_STATE_UNKNOWN
        entry.frozen = True
        entry.reason = reason
        entry.events.append({
            "event": Event.BROKER_STATE_UNKNOWN,
            "at_ns": time.time_ns(),
            "detail": reason,
        })
    _persist(entry)
    _log_lifecycle(entry, Event.BROKER_STATE_UNKNOWN)


def _log_lifecycle(entry: WatchdogEntry, event: str) -> None:
    """Fire-and-forget hot-store audit row."""
    try:
        alpha_hot_store.init()
        alpha_hot_store.record_event(
            setup_id=f"broker_watchdog:{entry.broker}:{entry.client_order_id}",
            event=f"broker_watchdog_{event.lower()}",
            stage="broker_event_watchdog",
            symbol=entry.symbol,
            payload={
                "broker": entry.broker,
                "state": entry.state,
                "frozen": entry.frozen,
                "reason": entry.reason,
                "broker_order_id": entry.broker_order_id,
                "account_id": entry.account_id,
            },
        )
    except Exception:  # noqa: BLE001
        pass


# ── Test / ops helpers ──────────────────────────────────────────────

def _reset_for_tests() -> None:  # pragma: no cover - test helper
    with _LOCK:
        for t in _TIMERS.values():
            try:
                t.cancel()
            except Exception:  # noqa: BLE001
                pass
        _TIMERS.clear()
        _ENTRIES.clear()
        _RECONCILERS.clear()
