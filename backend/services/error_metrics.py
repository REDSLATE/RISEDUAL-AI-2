"""In-process rolling counter of structured ERROR log events.

Every `log_error(logger, {...})` call in `services.structured_log`
automatically pushes a `(timestamp, context, type, note)` tuple into a
bounded deque here. The `/api/admin/gather-error-rate` admin endpoint
reads this buffer to surface a one-glance "which provider is flaking
right now" view without needing a log aggregator.

Design choices:
- **In-memory, process-local.** Deliberately avoids a Mongo write on
  every log line (hot path: price-provider + war_room fan-out). Restarts
  clear the buffer; that's fine because this is an operational
  observability tile, not an audit log.
- **Bounded at `MAX_EVENTS`.** A `collections.deque(maxlen=...)` is
  O(1) append and self-trimming, so the buffer can't grow unbounded
  under a storm of upstream failures.
- **Thread-safe.** Protected by a single `threading.Lock`. Log sites
  can be called from any thread (APScheduler jobs, FastAPI workers,
  `asyncio.to_thread` shims) — the lock is held only for the O(1)
  append / snapshot, so contention is negligible.
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

# 10k events ≈ a few days of steady state even for a busy app. At ~80
# bytes per tuple, that's under 1 MB resident — well below anything
# worth worrying about.
MAX_EVENTS = 10_000

_lock = threading.Lock()
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)


def record_error(
    context: str,
    err_type: str,
    note: str = "",
    **extra: Any,
) -> None:
    """Append one ERROR event. Called from `structured_log._emit`.
    Callers that fail to provide a `context` still get recorded under
    the empty string — easier than losing the event entirely."""
    with _lock:
        _events.append({
            "ts": datetime.now(timezone.utc),
            "context": context or "",
            "type": err_type or "",
            "note": note or "",
            "extra": extra,
        })


def snapshot_since(cutoff: datetime) -> list[dict[str, Any]]:
    """Return all events newer than `cutoff`. Caller owns the returned
    list (we build a fresh one under the lock, then release)."""
    with _lock:
        # Deque iteration is cheap but we copy into a list so the caller
        # can sort / group without holding the lock.
        return [e for e in _events if e["ts"] >= cutoff]


def clear() -> None:
    """Drop all buffered events. Used by tests to isolate cases."""
    with _lock:
        _events.clear()


def size() -> int:
    """Current event count (for tests / health checks)."""
    with _lock:
        return len(_events)
