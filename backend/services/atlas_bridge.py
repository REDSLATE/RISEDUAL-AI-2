"""RISEDUAL System Atlas bridge — fire-and-forget wrappers.

CRITICAL DOCTRINE
-----------------
Atlas is **structurally incapable** of blocking or slowing a trade.

Previous Atlas rollouts caused the trade path to hang / lag because
``AtlasLedger`` writes are synchronous SQLite calls that use
``BEGIN IMMEDIATE`` with a 5-second ``busy_timeout``. Under any lock
contention (multi-worker, WAL checkpoint, competing writer), that
5-second wait would freeze the trade path.

This bridge fixes that at the design level:

1. **No synchronous SQLite call is ever made from the caller's
   coroutine.** Every write is scheduled via ``asyncio.create_task``
   which offloads it to a thread executor.
2. **Every write has a 100 ms hard timeout.** If the ledger doesn't
   commit within 100 ms, the task is cancelled and a debug log is
   emitted. The trade continues untouched.
3. **Atlas is observation-only.** Duplicate suppression is NEVER
   enforced from this bridge — no gate here, ever. This is
   non-negotiable (see the overlay post-mortem doc, rule 8).
4. **Master kill switch**: ``RISEDUAL_ATLAS_ENABLED`` (default ON).
   Set to ``0`` to disable all Atlas activity without a code deploy.

See /app/docs/POSTMORTEM_ACCOUNT_AWARE_OVERLAY.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


# Global ledger singleton (installed at startup by server.py).
_LEDGER: Any = None

# Hard budget per Atlas write. If the ledger doesn't return in this
# many milliseconds, the task is cancelled and logged. Chosen to be
# well below any human-perceivable latency AND small enough that a
# stuck writer can't stack up back-pressure.
_WRITE_BUDGET_MS: int = 100


def _flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "on")


def atlas_enabled() -> bool:
    """Master kill switch. Default ON — flip
    ``RISEDUAL_ATLAS_ENABLED=0`` to disable without a code deploy."""
    return _flag("RISEDUAL_ATLAS_ENABLED", default=True)


def set_ledger(ledger: Any) -> None:
    global _LEDGER
    _LEDGER = ledger


def get_ledger() -> Any:
    return _LEDGER


def init_ledger(db_path: Optional[str] = None) -> Any:
    """Create the Atlas ledger at ``db_path`` (or
    ``RISEDUAL_ATLAS_DB_PATH`` / default ``/app/backend/var/risedual_atlas.sqlite3``).
    Returns the ledger instance or ``None`` on failure."""
    if not atlas_enabled():
        logger.info("[atlas_bridge] disabled via RISEDUAL_ATLAS_ENABLED — skipping init")
        return None
    try:
        from risedual_atlas import AtlasLedger
    except Exception as exc:  # noqa: BLE001
        logger.warning("[atlas_bridge] risedual_atlas import failed: %s", exc)
        return None
    path = (
        db_path
        or os.environ.get("RISEDUAL_ATLAS_DB_PATH")
        or "/app/backend/var/risedual_atlas.sqlite3"
    )
    try:
        ledger = AtlasLedger(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[atlas_bridge] AtlasLedger init failed at %s: %s", path, exc)
        return None
    set_ledger(ledger)
    logger.info("[atlas_bridge] Atlas ledger initialized at %s", path)
    return ledger


# ── Internal: bounded fire-and-forget scheduler ───────────────────────


def _schedule(coro_factory):
    """Create a background task that runs ``coro_factory()`` with a
    100 ms hard timeout. On timeout / exception, log at debug and
    move on. Returns the ``asyncio.Task`` (or ``None`` if no loop)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    async def _runner():
        try:
            await asyncio.wait_for(coro_factory(), timeout=_WRITE_BUDGET_MS / 1000.0)
        except asyncio.TimeoutError:
            logger.debug("[atlas_bridge] write exceeded %d ms budget — cancelled", _WRITE_BUDGET_MS)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[atlas_bridge] background write failed (non-fatal): %s", exc)

    return loop.create_task(_runner())


def _to_thread(fn, *args, **kwargs):
    return asyncio.to_thread(fn, *args, **kwargs)


# ── Fingerprint builder (pure, no I/O) ────────────────────────────────


def _build_payload(intent: Mapping[str, Any]) -> dict[str, Any]:
    symbol = str(intent.get("symbol") or "").upper()
    direction = str(intent.get("direction") or intent.get("action") or "").upper()
    side = (
        "BUY"
        if direction in {"BUY", "LONG", "STRONG_BUY", "WEAK_BUY", "UP", "BULLISH"}
        else "SELL"
        if direction in {"SELL", "SHORT", "STRONG_SELL", "WEAK_SELL", "DOWN", "BEARISH"}
        else direction
    )
    opportunity_id = str(
        intent.get("opportunity_id")
        or intent.get("prediction_id")
        or intent.get("setup_id")
        or f"scan:{intent.get('scan_id') or 'unknown'}:{symbol}"
    )
    return {
        "intent_id": str(intent.get("intent_id") or f"pex:{int(time.time_ns())}:{symbol}"),
        "stack": "mission_control",
        "lane": "equity",
        "broker": "public",
        "broker_account_ref": os.environ.get("RISEDUAL_ATLAS_ACCOUNT_REF", "public-primary"),
        "symbol": symbol,
        "side": side,
        "strategy": str(intent.get("strategy_id") or "signal_dispatcher:v1"),
        "opportunity_id": opportunity_id,
    }


# ── Public API ────────────────────────────────────────────────────────


def observe_intent_async(intent: Mapping[str, Any]) -> Optional[str]:
    """Fire-and-forget: record the intent in Atlas. Returns the
    intent_id we WOULD claim (for later transition calls) without
    waiting for the ledger.

    NEVER blocks, NEVER gates, NEVER raises. Duplicate suppression
    is intentionally not enforced — the ledger's UNIQUE constraint
    will silently reject a duplicate write inside the background
    task, and the caller's trade proceeds untouched.
    """
    if not atlas_enabled():
        return None
    ledger = _LEDGER
    if ledger is None:
        return None

    try:
        payload = _build_payload(intent)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[atlas_bridge] payload build failed (non-fatal): %s", exc)
        return None

    async def _do_claim():
        from risedual_atlas.integration import claim_intent_mapping
        await _to_thread(claim_intent_mapping, ledger, payload)

    _schedule(_do_claim)
    return payload["intent_id"]


def transition_async(
    intent_id: Optional[str],
    to_status: str,
    *,
    broker_order_id: Optional[str] = None,
    reason_code: Optional[str] = None,
) -> None:
    """Fire-and-forget lifecycle transition. Silent on failure."""
    if not atlas_enabled() or _LEDGER is None or not intent_id:
        return
    ledger = _LEDGER

    async def _do_transition():
        from risedual_atlas import IntentStatus

        await _to_thread(
            ledger.transition_intent,
            intent_id,
            IntentStatus(to_status),
            broker_order_id=broker_order_id,
            reason_code=reason_code,
        )

    _schedule(_do_transition)


def trace_cycle_async(
    *,
    stack: str = "mission_control",
    lane: Optional[str] = None,
    symbol: Optional[str] = None,
    stages: Optional[list[dict[str, Any]]] = None,
    terminal_result: str = "NO_SETUP",
    reason_code: Optional[str] = None,
    intent_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """Fire-and-forget: record an already-complete cycle in one write.

    Unlike the vendored ``CycleTrace`` context manager (which does
    inline SQLite writes on every ``mark()``), this API collects
    stages in memory and writes the whole trace as a single
    background task. If it exceeds the 100 ms budget, it's cancelled
    silently.

    ``stages`` is a list of dicts with keys::

        {"stage": "market_event", "outcome": "observed",
         "timestamp_ns": 1234, "reason_code": None, "details": {...}}
    """
    if not atlas_enabled() or _LEDGER is None:
        return
    ledger = _LEDGER
    stages_copy = list(stages or [])

    async def _do_trace():
        import uuid as _uuid
        from risedual_atlas import TerminalResult, TraceEvent

        trace_id = f"cycle:{_uuid.uuid4().hex}"

        def _write():
            ledger.start_trace(
                trace_id,
                stack=stack,
                lane=lane,
                symbol=symbol,
                correlation_id=correlation_id,
                started_ns=(
                    stages_copy[0]["timestamp_ns"]
                    if stages_copy and stages_copy[0].get("timestamp_ns")
                    else time.time_ns()
                ),
            )
            for s in stages_copy:
                ledger.append_trace_event(
                    trace_id,
                    TraceEvent(
                        stage=str(s.get("stage") or "observed"),
                        timestamp_ns=int(s.get("timestamp_ns") or time.time_ns()),
                        outcome=str(s.get("outcome") or "observed"),
                        reason_code=s.get("reason_code"),
                        details=s.get("details") or {},
                    ),
                )
            ledger.finalize_trace(
                trace_id,
                TerminalResult(terminal_result),
                reason_code=reason_code,
                intent_id=intent_id,
            )

        await _to_thread(_write)

    _schedule(_do_trace)


__all__ = [
    "atlas_enabled",
    "get_ledger",
    "init_ledger",
    "observe_intent_async",
    "set_ledger",
    "trace_cycle_async",
    "transition_async",
]
