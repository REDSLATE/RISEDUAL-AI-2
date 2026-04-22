"""Global kill-switch engine.

A fleet-wide circuit-breaker that **halts all new signal execution**
when either:

  * the aggregate equity curve has drawn down past
    :data:`MAX_DRAWDOWN_KILL` (default 25%), OR
  * the rolling error rate over the last :data:`ERROR_WINDOW`
    executions has exceeded :data:`MAX_ERROR_RATE` (default 30%).

Once tripped, the switch enters a :data:`COOLDOWN_SECONDS` (default
5 min) lockout during which every guarded call short-circuits. After
the cooldown expires the next guarded call transparently
re-evaluates; it stays tripped only if the underlying condition still
holds. Operators can force-clear via the admin endpoint at any time.

Design choices:
  * **Thread-safe singleton** — signal execution fans out across
    APScheduler jobs + FastAPI workers + `asyncio.to_thread` shims.
    A single `threading.Lock` protects the small critical sections
    (error-window append, flag flip, cooldown read).
  * **UTC-aware timestamps** — `datetime.now(timezone.utc)`
    everywhere. Mixing naive + aware datetimes is a recurring bug
    class in this codebase; the invariant is enforced here.
  * **Structured logging via `log_error`** — any trip, cooldown
    expiry, or manual reset emits a single JSON-parseable line with
    `context=kill_switch`.
  * **`guarded_execute` accepts both sync and async callables** —
    so it can wrap `execute_signal` (async) and a backtest tick
    (sync) without two code paths.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from services.structured_log import log_error, log_info

logger = logging.getLogger(__name__)


def _env_float(key: str, default: float) -> float:
    """Read a float from env or fall back. Silent on parse errors
    so bad env values don't bring down the import path."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# ────────────────────────────────────────────────────────────────────────────
# Config — env-tunable so operators can dial thresholds without redeploy
# ────────────────────────────────────────────────────────────────────────────

MAX_DRAWDOWN_KILL = _env_float("KILL_SWITCH_MAX_DRAWDOWN", 0.25)
MAX_ERROR_RATE = _env_float("KILL_SWITCH_MAX_ERROR_RATE", 0.30)
ERROR_WINDOW = _env_int("KILL_SWITCH_ERROR_WINDOW", 50)
COOLDOWN_SECONDS = _env_int("KILL_SWITCH_COOLDOWN_SECONDS", 300)


class KillSwitch:
    """Thread-safe rolling-error + drawdown circuit breaker.

    Instance-based rather than module-global so tests can spin up a
    fresh one without cross-contamination, but the module exports a
    singleton :data:`kill_switch` for production callers.
    """

    def __init__(
        self,
        max_drawdown: float = MAX_DRAWDOWN_KILL,
        max_error_rate: float = MAX_ERROR_RATE,
        error_window: int = ERROR_WINDOW,
        cooldown_seconds: int = COOLDOWN_SECONDS,
    ) -> None:
        self.max_drawdown = max_drawdown
        self.max_error_rate = max_error_rate
        self.error_window = error_window
        self.cooldown_seconds = cooldown_seconds

        self._lock = threading.Lock()
        self._error_log: deque[bool] = deque(maxlen=error_window)
        self._active = False
        self._tripped_at: datetime | None = None
        self._last_reason: str | None = None
        self._trip_count = 0

    # ── error-window maintenance ────────────────────────────────────

    def record_result(self, success: bool) -> None:
        """Append one execution outcome. `True` on success, `False`
        on any handled or unhandled error. Cap is enforced by the
        deque's `maxlen`, so the call is O(1)."""
        with self._lock:
            self._error_log.append(not success)

    def error_rate(self) -> float:
        """Current error rate in [0.0, 1.0]. Returns 0.0 on an empty
        window (no executions yet → not tripping on zero data)."""
        with self._lock:
            if not self._error_log:
                return 0.0
            return sum(self._error_log) / len(self._error_log)

    # ── trip evaluation ─────────────────────────────────────────────

    def should_trip(self, drawdown: float | None = None) -> tuple[bool, str]:
        """Evaluate trip conditions. Returns ``(trip, reason)``.
        `drawdown` is optional — pass it when you have a fresh equity
        curve; pass `None` to evaluate error rate only.
        """
        if drawdown is not None and drawdown >= self.max_drawdown:
            return True, f"drawdown {drawdown:.2%} ≥ {self.max_drawdown:.0%}"
        rate = self.error_rate()
        if rate >= self.max_error_rate and len(self._error_log) >= 5:
            # Require ≥5 samples so an early fluke (1/1 = 100%) can't
            # trip the whole fleet before we have a signal.
            return True, f"error rate {rate:.2%} ≥ {self.max_error_rate:.0%}"
        return False, ""

    # ── flag management ─────────────────────────────────────────────

    def activate(self, reason: str) -> None:
        """Flip the switch to active. Idempotent — re-activating an
        already-active switch just refreshes the reason + timestamp
        (useful when a second breach happens during cooldown)."""
        with self._lock:
            self._active = True
            self._tripped_at = datetime.now(timezone.utc)
            self._last_reason = reason
            self._trip_count += 1
        log_error(logger, {
            "context": "kill_switch",
            "type": "KillSwitchTripped",
            "error": reason,
            "note": "all guarded execution halted",
        })

    def deactivate(self, reason: str = "manual reset") -> None:
        with self._lock:
            self._active = False
            self._tripped_at = None
        log_info(logger, {
            "context": "kill_switch",
            "type": "KillSwitchCleared",
            "error": reason,
        })

    def is_active(self) -> bool:
        """True iff the switch is tripped AND cooldown hasn't expired.
        Auto-clears on cooldown expiry so callers don't need to poll
        a separate method — the next `is_active()` after cooldown
        returns `False` and emits the cleared-log line as a side
        effect."""
        with self._lock:
            if not self._active:
                return False
            if self._tripped_at is None:
                # Defensive — can't happen with normal activate flow,
                # but clear the inconsistent state rather than lock
                # the fleet out forever.
                self._active = False
                return False
            elapsed = (
                datetime.now(timezone.utc) - self._tripped_at
            ).total_seconds()
            if elapsed >= self.cooldown_seconds:
                # Release the lock before calling deactivate which
                # re-acquires it — Python's Lock isn't reentrant.
                should_clear = True
            else:
                should_clear = False
        if should_clear:
            self.deactivate(reason="cooldown expired")
            return False
        return True

    # ── observability snapshot ──────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Full state for the admin endpoint. Safe to call at any
        rate — does not mutate state (no auto-clear side effect
        here; that's reserved for `is_active()` on the hot path)."""
        with self._lock:
            tripped_at = self._tripped_at
            active = self._active
            reason = self._last_reason
            trip_count = self._trip_count
            window_len = len(self._error_log)
            rate = (sum(self._error_log) / window_len) if window_len else 0.0

        cooldown_remaining = 0
        if active and tripped_at is not None:
            elapsed = (
                datetime.now(timezone.utc) - tripped_at
            ).total_seconds()
            cooldown_remaining = max(0, int(self.cooldown_seconds - elapsed))

        return {
            "active": active,
            "tripped_at": tripped_at.isoformat() if tripped_at else None,
            "last_reason": reason,
            "trip_count": trip_count,
            "cooldown_remaining_seconds": cooldown_remaining,
            "error_rate": round(rate, 4),
            "error_window_size": window_len,
            "config": {
                "max_drawdown": self.max_drawdown,
                "max_error_rate": self.max_error_rate,
                "error_window": self.error_window,
                "cooldown_seconds": self.cooldown_seconds,
            },
        }

    def reset(self) -> None:
        """Force-clear state — admin override. Wipes the error window
        too; the alternative (clear flag only) means the fleet is
        immediately re-trippable on the same stale errors, which
        defeats the point of a manual reset."""
        with self._lock:
            self._active = False
            self._tripped_at = None
            self._error_log.clear()
        log_info(logger, {
            "context": "kill_switch",
            "type": "KillSwitchReset",
            "error": "admin override",
        })


# ────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ────────────────────────────────────────────────────────────────────────────

kill_switch = KillSwitch()


async def guarded_execute(
    execute_fn: Callable[..., Any],
    *args: Any,
    drawdown: float | None = None,
    switch: KillSwitch | None = None,
    **kwargs: Any,
) -> Any:
    """Wrap any callable (sync or async) with kill-switch protection.

    Short-circuits to ``{"skipped": True, "reason": ...}`` when the
    switch is active or would trip on the supplied drawdown, without
    ever invoking `execute_fn`. On successful or exception-raising
    execution, records the outcome in the rolling window so the
    error-rate branch can trip on real signal.

    Accepts both coroutine functions (``async def``) and plain
    callables — detected via `inspect.iscoroutinefunction`. That
    keeps us compatible with `execute_signal` (async) and backtest
    ticks (sync) without two code paths.

    `switch` defaults to the module singleton; tests pass a fresh
    instance to isolate state.
    """
    ks = switch if switch is not None else kill_switch

    # 1. Already tripped?  → short-circuit.
    if ks.is_active():
        status = ks.status()
        return {
            "skipped": True,
            "reason": "kill switch active",
            "cooldown_remaining_seconds": status["cooldown_remaining_seconds"],
            "last_reason": status["last_reason"],
        }

    # 2. Fresh drawdown data breaches threshold?  → trip + skip.
    trip, reason = ks.should_trip(drawdown=drawdown)
    if trip:
        ks.activate(reason)
        return {"skipped": True, "reason": f"kill switch tripped: {reason}"}

    # 3. Execute. Record outcome regardless of success path so the
    #    error-rate window sees reality.
    try:
        if inspect.iscoroutinefunction(execute_fn):
            result = await execute_fn(*args, **kwargs)
        else:
            result = execute_fn(*args, **kwargs)
    except asyncio.CancelledError:
        # Cancellation is not a logical error — don't count against
        # the error window. Re-raise so the caller's task actually
        # cancels.
        raise
    except Exception as exc:  # noqa: BLE001
        ks.record_result(success=False)
        log_error(logger, {
            "context": "kill_switch",
            "type": type(exc).__name__,
            "error": str(exc),
            "note": "guarded_execute caught",
        })
        # Final re-evaluation: this single error may have just pushed
        # us over the threshold. Trip eagerly.
        trip, reason = ks.should_trip()
        if trip:
            ks.activate(reason)
        return {"skipped": True, "reason": "execution error"}

    # 4. Interpret broker-style ``{"error": ...}`` / ``{"skipped":
    #    True}`` dicts as unsuccessful so the error window reflects
    #    broker 4xx/5xx, not just uncaught exceptions.
    is_failure = (
        isinstance(result, dict)
        and (result.get("error") is not None or result.get("skipped") is True)
    )
    ks.record_result(success=not is_failure)

    if is_failure:
        # After a broker-level failure, re-check the rate in case we
        # just tipped over — same eager-trip behaviour as the
        # exception branch above.
        trip, reason = ks.should_trip()
        if trip:
            ks.activate(reason)

    return result
