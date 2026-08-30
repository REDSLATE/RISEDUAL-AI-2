"""Broker execution circuit breaker.

Why this exists
---------------
Public.com (and any other execution broker) rate-limits, has
partial symbol coverage, and occasionally 5xx. Without a circuit
breaker every ``fetch_broker_quote`` call still hits the broker,
burning quota and starving the tick loop with 10s timeouts every
few seconds when the broker is unhappy.

The breaker fronts every broker call:

* **CLOSED**   — broker is healthy; every call goes through and is
                 recorded as ``success``/``failure``. If failures
                 in the rolling window exceed ``ERROR_THRESHOLD``,
                 we trip to OPEN.
* **OPEN**     — broker is degraded; the pool's
                 ``fetch_broker_quote`` short-circuits to ``None``
                 immediately (no HTTP), and callers on the
                 execution-quote path fall back to vendors and mark
                 the result ``broker_degraded`` so auto-execute is
                 blocked (we NEVER submit an order without a broker
                 quote; this preserves the guarantee even when the
                 broker is down).
* **HALF_OPEN** — after ``COOLDOWN_SECONDS`` in OPEN, the next
                 broker call is allowed through as a probe. Success
                 closes the circuit; another failure re-opens for
                 the full cooldown.

State lives in a module-level singleton with a lock — safe under
uvicorn's asyncio loop (single event loop). No SQLite / Mongo — we
want the check to cost nanoseconds because it runs on the hot path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Optional
import logging
import os
import time

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"       # healthy — every call goes through
    OPEN = "open"           # degraded — every call short-circuits
    HALF_OPEN = "half_open"  # probe — one call allowed


def _env_int(key: str, default: int) -> int:
    """Read an integer env var, returning ``default`` on bad values.

    Kept tolerant so a rogue value can't break the trade path — the
    breaker is a safety layer, its own config being wrong must not
    take Alpha down with it.
    """
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("[broker_cb] bad int for %s=%r; using %d", key, raw, default)
        return default


# Rolling-window failure threshold. Fifth broker error inside the
# window trips the breaker. Kept small so a single flapping instance
# doesn't spam Alpha with 10s timeouts for minutes.
ERROR_THRESHOLD: int = _env_int("BROKER_CB_ERROR_THRESHOLD", 5)
# How far back to look when counting failures.
WINDOW_SECONDS: int = _env_int("BROKER_CB_WINDOW_SECONDS", 300)
# Time OPEN before the next probe attempt.
COOLDOWN_SECONDS: int = _env_int("BROKER_CB_COOLDOWN_SECONDS", 120)


@dataclass
class _BreakerState:
    """In-memory breaker state. Not persisted — a process restart
    resets to CLOSED, which is the correct behaviour (the pod is
    fresh, we should give the broker a fresh chance)."""
    state: CircuitState = CircuitState.CLOSED
    # Timestamps of failures inside the current rolling window.
    # A ``deque`` would be faster but the list stays small (≤ ERROR_
    # THRESHOLD) because we prune on every recording call.
    failures: list[float] = field(default_factory=list)
    # Consecutive successes since we last entered HALF_OPEN or
    # CLOSED. Displayed in the admin endpoint so an operator can
    # tell the breaker's actually seeing healthy traffic.
    consecutive_successes: int = 0
    opened_at: Optional[float] = None
    last_reason: Optional[str] = None


_STATE = _BreakerState()
_LOCK = Lock()


def _prune(now: float) -> None:
    """Drop failure records older than the rolling window.

    Caller holds ``_LOCK``.
    """
    cutoff = now - WINDOW_SECONDS
    _STATE.failures = [t for t in _STATE.failures if t >= cutoff]


def _transition_if_needed(now: float) -> None:
    """Move state forward when time / counters demand it.

    Caller holds ``_LOCK``.
    """
    if (_STATE.state is CircuitState.OPEN
            and _STATE.opened_at is not None
            and now - _STATE.opened_at >= COOLDOWN_SECONDS):
        _STATE.state = CircuitState.HALF_OPEN
        _STATE.last_reason = "cooldown_elapsed"
        logger.info("[broker_cb] cooldown elapsed → HALF_OPEN")


# ─────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────
def allow_call() -> bool:
    """Should the next broker call be attempted?

    Callers on the hot path check this BEFORE issuing an HTTP
    request. Returns:

    * ``True``  — CLOSED (normal) or HALF_OPEN (probe allowed).
    * ``False`` — OPEN and cooldown not yet elapsed. Caller must
                  treat this as if the broker returned ``None``
                  (no quote) and let the vendor path serve.

    Never blocks; never raises.
    """
    with _LOCK:
        now = time.time()
        _prune(now)
        _transition_if_needed(now)
        return _STATE.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)


def record_success() -> None:
    """A broker call returned a usable payload.

    HALF_OPEN → CLOSED. CLOSED stays CLOSED but the success is
    counted for observability.
    """
    with _LOCK:
        _STATE.consecutive_successes += 1
        if _STATE.state is CircuitState.HALF_OPEN:
            _STATE.state = CircuitState.CLOSED
            _STATE.failures.clear()
            _STATE.opened_at = None
            _STATE.last_reason = "probe_succeeded"
            logger.info("[broker_cb] probe succeeded → CLOSED")


def record_failure(reason: str = "unknown") -> None:
    """A broker call failed / returned no data.

    Records the timestamp. If the rolling-window count crosses
    ``ERROR_THRESHOLD`` (from CLOSED) or the probe fails (from
    HALF_OPEN), the breaker trips to OPEN.
    """
    with _LOCK:
        now = time.time()
        _prune(now)
        _STATE.consecutive_successes = 0

        # A probe failure re-opens the circuit immediately, no need
        # to wait for the count to build back up.
        if _STATE.state is CircuitState.HALF_OPEN:
            _STATE.state = CircuitState.OPEN
            _STATE.opened_at = now
            _STATE.last_reason = f"probe_failed:{reason}"
            logger.warning("[broker_cb] probe failed → OPEN (reason=%s)", reason)
            return

        _STATE.failures.append(now)
        if (_STATE.state is CircuitState.CLOSED
                and len(_STATE.failures) >= ERROR_THRESHOLD):
            _STATE.state = CircuitState.OPEN
            _STATE.opened_at = now
            _STATE.last_reason = (
                f"threshold_exceeded:{len(_STATE.failures)}/"
                f"{ERROR_THRESHOLD} in {WINDOW_SECONDS}s"
            )
            logger.warning(
                "[broker_cb] %d failures in %ds → OPEN (reason=%s)",
                len(_STATE.failures), WINDOW_SECONDS, reason,
            )


def snapshot() -> dict:
    """JSON-serializable state for the admin endpoint."""
    with _LOCK:
        now = time.time()
        _prune(now)
        _transition_if_needed(now)
        opened_at = _STATE.opened_at
        cooldown_remaining = None
        if _STATE.state is CircuitState.OPEN and opened_at is not None:
            cooldown_remaining = max(
                0.0, COOLDOWN_SECONDS - (now - opened_at),
            )
        return {
            "state": _STATE.state.value,
            "failures_in_window": len(_STATE.failures),
            "error_threshold": ERROR_THRESHOLD,
            "window_seconds": WINDOW_SECONDS,
            "cooldown_seconds": COOLDOWN_SECONDS,
            "cooldown_remaining_seconds": cooldown_remaining,
            "consecutive_successes": _STATE.consecutive_successes,
            "opened_at": opened_at,
            "last_reason": _STATE.last_reason,
        }


def reset() -> dict:
    """Force the breaker back to CLOSED. Only exposed via the
    admin endpoint — an operator manually clearing a wedged
    breaker (e.g. after acknowledging a broker outage was
    transient). Returns the fresh snapshot.
    """
    with _LOCK:
        _STATE.state = CircuitState.CLOSED
        _STATE.failures.clear()
        _STATE.opened_at = None
        _STATE.last_reason = "manual_reset"
        _STATE.consecutive_successes = 0
    return snapshot()


# Test-only helper — resets the module-level singleton so unit
# tests can't leak state into each other. Not exposed via any
# route. Prefixed ``_`` to signal "test-scaffolding".
def _reset_for_tests() -> None:
    with _LOCK:
        _STATE.state = CircuitState.CLOSED
        _STATE.failures.clear()
        _STATE.opened_at = None
        _STATE.last_reason = None
        _STATE.consecutive_successes = 0


__all__ = [
    "CircuitState",
    "ERROR_THRESHOLD",
    "WINDOW_SECONDS",
    "COOLDOWN_SECONDS",
    "allow_call",
    "record_success",
    "record_failure",
    "snapshot",
    "reset",
]
