"""MC sidecar — in-process asyncio start / stop / status for Alpha.

This is the in-process counterpart to ``backend/sovereign/sidecar.py``
(the supervisor-managed external script). The in-process variant
historically also published contribution payloads, but since the
2026-02-23 alignment with MC's empty-payload 422 enforcement the
**contribution loop has been removed**. The supervisor-run
``sovereign.sidecar`` is now the **canonical** contribution
producer — single source of truth, deterministic outcome-inbox
drain, real LocalState-backed content, doctrinal empty-payload
refusal.

What's left in-process:
  * Heartbeat loop  — publishes liveness to ``/api/heartbeat-ping``.
  * Watchdog loop   — SIGKILLs the process group when the liveness
                      file goes stale (GIL-stall / native-extension
                      freeze defence the supervisor watchdog can't
                      observe).

The heartbeat in-process is **redundant on purpose**: the supervisor
sidecar publishes its own heartbeat too. Two independent paths means
MC still sees liveness even if one path dies — that's the right
belt-and-suspenders surface, at the heartbeat layer where it matters,
not at the contribution layer where racing causes data corruption.

Why an in-process variant exists
--------------------------------
* No second supervisor program to babysit (`alpha-sidecar` +
  `alpha-liveness-watcher` were two separate processes that need to
  stay in lockstep with the backend's deploy cycle).
* Lifespan-aware — when FastAPI shuts down cleanly, the loops drain
  cleanly via ``asyncio.CancelledError`` instead of being SIGKILLed.
* Status / start / stop endpoints can introspect the actual task
  objects, not just a supervisor "RUNNING" badge that lies during a
  GIL stall.
* Symmetric with REDEYE's pattern — same start/stop/status surface
  so MC's brain-operator dashboard treats every brain identically.

Coexistence with the external sidecar
-------------------------------------
By default ``ALPHA_INPROCESS_SIDECAR`` is **off**. The external
supervisor sidecar (``/etc/supervisor/conf.d/alpha-sidecar.conf``)
keeps running as it does today. To switch to in-process heartbeat /
watchdog as well, set ``ALPHA_INPROCESS_SIDECAR=1``. Either way the
supervisor sidecar remains the contribution producer.

Env vars (operator-facing surface)
----------------------------------
=================================== ==================== ============================
Var                                  Default              Effect
=================================== ==================== ============================
``ALPHA_BRAIN_NAME``                 ``alpha``            Liveness file: ``/tmp/<n>_alive``
``ALPHA_LIVENESS_FILE``              ``/tmp/alpha_alive`` Explicit path override
``ALPHA_HEARTBEAT_INTERVAL_S``       ``30``               Heartbeat loop cadence
``ALPHA_WATCHDOG_ENABLED``           ``true``             Disable for local dev
``ALPHA_WATCHDOG_INTERVAL_S``        ``30``               Watchdog check cadence
``ALPHA_WATCHDOG_STALE_S``           ``120``              SIGKILL threshold (seconds)
``ALPHA_INPROCESS_SIDECAR``          ``0``                Master enable for lifespan
=================================== ==================== ============================

Doctrine pinned in code (not configurable on purpose)
-----------------------------------------------------
* Heartbeat ALWAYS runs on its own task and its own httpx client —
  a hung anything can never starve it (the 2026-05-14
  silent-freeze RCA).
* Contribution is owned by ``sovereign.sidecar`` (supervisor) — the
  in-process module MUST NOT post contributions. Doing so would
  re-introduce the inbox-drain race condition deleted on 2026-02-23.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from services import mc_sovereign

logger = logging.getLogger(__name__)


# ── env-driven config ──────────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name, "") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


BRAIN_NAME = os.environ.get("ALPHA_BRAIN_NAME") or mc_sovereign.DEFAULT_BRAIN_NAME
HEARTBEAT_INTERVAL_S = _env_int("ALPHA_HEARTBEAT_INTERVAL_S", 30)
# ``CONTRIBUTION_INTERVAL_S`` is intentionally retained for any
# operator dashboard / REDEYE-shaped status parser still reading it,
# but the contribution loop itself was deleted on 2026-02-23 — see
# the module docstring. The supervisor sidecar owns contributions.
CONTRIBUTION_INTERVAL_S = _env_int("ALPHA_CONTRIBUTION_INTERVAL_S", 60)
WATCHDOG_ENABLED = _env_bool("ALPHA_WATCHDOG_ENABLED", True)
WATCHDOG_INTERVAL_S = _env_int("ALPHA_WATCHDOG_INTERVAL_S", 30)
WATCHDOG_STALE_S = _env_int("ALPHA_WATCHDOG_STALE_S", 120)
LIVENESS_FILE = Path(
    os.environ.get("ALPHA_LIVENESS_FILE") or f"/tmp/{BRAIN_NAME}_alive"
)

# State persistence (mirrors REDEYE's pattern).
STATE_COLLECTION = "mc_sidecar_state"
_state_doc_id = f"alpha:{BRAIN_NAME}"


# ── task globals ───────────────────────────────────────────────────────


_heartbeat_task: asyncio.Task | None = None
_watchdog_task: asyncio.Task | None = None
_task_lock = asyncio.Lock()


# ── helpers ────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _liveness_age_s() -> float | None:
    """Seconds since the liveness file was last touched, or ``None``
    if the file doesn't exist yet."""
    try:
        if not LIVENESS_FILE.exists():
            return None
        mtime = LIVENESS_FILE.stat().st_mtime
        return max(0.0, datetime.now(timezone.utc).timestamp() - mtime)
    except OSError:
        return None


def _touch_liveness() -> None:
    """Stamp the liveness file. Watchdog reads its mtime to decide
    whether to SIGKILL the process group."""
    try:
        LIVENESS_FILE.touch()
    except OSError as exc:
        logger.warning("[mc_sidecar] liveness touch failed: %s", exc)


async def _set_state(db, **fields: Any) -> None:
    """Persist sidecar state to MongoDB (one doc per brain). Used by
    the admin status endpoint so a restarted backend can still answer
    "when did the sidecar last start?" without grepping logs.

    Best-effort — a Mongo hiccup must not crash a loop.
    """
    try:
        await db[STATE_COLLECTION].update_one(
            {"id": _state_doc_id},
            {"$set": {**fields, "id": _state_doc_id, "brain": BRAIN_NAME,
                      "updated_at": _now_iso()}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[mc_sidecar] state persist skipped: %s", exc)


def _mc_base() -> str:
    return (os.environ.get("MC_BASE_URL") or "").rstrip("/")


def _runtime_token() -> str:
    return os.environ.get("ALPHA_INGEST_TOKEN", "")


def _async_client() -> httpx.AsyncClient:
    """Fresh async client per loop iteration. No keep-alive — same
    half-open-socket defence as the sync client. The cost of a
    fresh handshake at 30–60 s cadence is negligible vs. a silent
    freeze."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=3.0, read=6.0, write=5.0, pool=2.0),
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=4),
    )


# ── transport loops ────────────────────────────────────────────────────


async def _heartbeat_loop(db) -> None:
    """The dumb, never-blocks-on-compute heartbeat publisher.

    Owns its own client (its own connection pool) so a hung
    contribution can't starve it. The 2026-05-14 silent-freeze
    invariant — heartbeat MUST stay alive even when other paths hang.
    """
    base = _mc_base()
    token = _runtime_token()
    if not base:
        logger.warning("[mc_sidecar] heartbeat loop: MC_BASE_URL unset, idling")
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)

    url = f"{base}/api/heartbeat-ping/{BRAIN_NAME}"
    headers = {"X-Runtime-Token": token, "Content-Type": "application/json"}
    logger.info("[mc_sidecar] heartbeat loop starting: every %ds", HEARTBEAT_INTERVAL_S)
    while True:
        try:
            async with _async_client() as client:
                r = await client.post(url, json={"ok": True}, headers=headers)
            if r.status_code >= 400:
                logger.warning(
                    "[mc_sidecar] heartbeat HTTP %d: %s",
                    r.status_code, (r.text or "")[:120],
                )
                await _set_state(
                    db, last_heartbeat_error=f"http_{r.status_code}",
                    last_heartbeat_error_at=_now_iso(),
                )
            else:
                _touch_liveness()
                await _set_state(
                    db, last_heartbeat_at=_now_iso(),
                    last_heartbeat_error=None,
                )
        except (httpx.HTTPError, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                logger.info("[mc_sidecar] heartbeat loop cancelled")
                raise
            logger.warning("[mc_sidecar] heartbeat failed: %s", exc)
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


async def _watchdog_loop(db) -> None:
    """SIGKILL the process group when the liveness file goes stale.

    Catches the GIL-deadlock / native-extension-stall class of freeze
    that the in-process watchdog thread can't observe. We mirror the
    external bash watcher's behaviour but stay in-process so the same
    asyncio supervisor manages it.

    Uses ``os.kill(os.getpid(), SIGKILL)`` so supervisor / uvicorn's
    parent process respawns us — no graceful cleanup because by
    definition we're already wedged.
    """
    logger.info(
        "[mc_sidecar] watchdog starting: check every %ds, stale threshold %ds",
        WATCHDOG_INTERVAL_S, WATCHDOG_STALE_S,
    )
    while True:
        try:
            age = _liveness_age_s()
            if age is not None and age > WATCHDOG_STALE_S:
                logger.error(
                    "[mc_sidecar] WATCHDOG: liveness %.1fs stale (limit %ds) — "
                    "SIGKILL self, expecting supervisor respawn",
                    age, WATCHDOG_STALE_S,
                )
                await _set_state(db, last_watchdog_kill_at=_now_iso(),
                                 last_watchdog_age_s=age)
                # SIGKILL is intentional — graceful shutdown can't work
                # if the heartbeat thread is wedged; the parent
                # process manager will respawn.
                os.kill(os.getpid(), signal.SIGKILL)
                return  # not reached
        except asyncio.CancelledError:
            logger.info("[mc_sidecar] watchdog cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[mc_sidecar] watchdog tick failed: %s", exc)
        await asyncio.sleep(WATCHDOG_INTERVAL_S)


# ── public surface (start / stop / status) ─────────────────────────────


async def start(db) -> dict[str, Any]:
    """Start heartbeat + watchdog loops. Idempotent.

    Contribution loop is intentionally NOT spawned — the supervisor
    sidecar (``sovereign.sidecar``) is the canonical contribution
    producer since 2026-02-23. See module docstring.

    Mirrors REDEYE's start() — same shape, same return value.
    """
    global _heartbeat_task, _watchdog_task
    async with _task_lock:
        if _heartbeat_task is None or _heartbeat_task.done():
            _heartbeat_task = asyncio.create_task(_heartbeat_loop(db))
        if WATCHDOG_ENABLED and (_watchdog_task is None or _watchdog_task.done()):
            _watchdog_task = asyncio.create_task(_watchdog_loop(db))
        await _set_state(db, status="running", started_at=_now_iso(),
                         last_error=None)
    return await status(db)


async def stop(db) -> dict[str, Any]:
    """Cancel all loops. Idempotent."""
    global _heartbeat_task, _watchdog_task
    async with _task_lock:
        for task_ref in (_heartbeat_task, _watchdog_task):
            if task_ref is not None and not task_ref.done():
                task_ref.cancel()
                try:
                    await task_ref
                except (asyncio.CancelledError, Exception):
                    pass
        _heartbeat_task = _watchdog_task = None
        await _set_state(db, status="stopped")
    return await status(db)


async def status(db) -> dict[str, Any]:
    """Read-only introspection — what the admin endpoint serves."""
    doc = await db[STATE_COLLECTION].find_one(
        {"id": _state_doc_id}, {"_id": 0},
    ) or {}
    return {
        **doc,
        # Doctrine 2026-02-23: contribution is owned by the supervisor
        # sidecar. We surface ``contribution_task_alive=False`` as a
        # constant so legacy REDEYE-shaped MC dashboards keep
        # parsing without erroring; the supervisor's contribution
        # status is read via /api/admin/sovereign-status instead.
        "contribution_task_alive": False,
        "contribution_owned_by": "supervisor:alpha-sidecar",
        "heartbeat_task_alive": bool(
            _heartbeat_task and not _heartbeat_task.done()
        ),
        "watchdog_task_alive": bool(
            _watchdog_task and not _watchdog_task.done()
        ),
        # Legacy alias — REDEYE's status payload exposes ``task_alive``
        # for older MC dashboards. Keep it so MC's parser stays simple.
        # Reflects heartbeat liveness only now (contribution moved out).
        "task_alive": bool(
            _heartbeat_task and not _heartbeat_task.done()
        ),
        "intervals": {
            "heartbeat_s": HEARTBEAT_INTERVAL_S,
            "contribution_s": CONTRIBUTION_INTERVAL_S,
            "watchdog_s": WATCHDOG_INTERVAL_S,
        },
        "watchdog": {
            "enabled": WATCHDOG_ENABLED,
            "stale_threshold_s": WATCHDOG_STALE_S,
            "liveness_file": str(LIVENESS_FILE),
            "liveness_age_s": _liveness_age_s(),
        },
        "identity": mc_sovereign.brain_identity(),
    }


__all__ = [
    "BRAIN_NAME",
    "CONTRIBUTION_INTERVAL_S",
    "HEARTBEAT_INTERVAL_S",
    "LIVENESS_FILE",
    "STATE_COLLECTION",
    "WATCHDOG_ENABLED",
    "WATCHDOG_INTERVAL_S",
    "WATCHDOG_STALE_S",
    "start",
    "status",
    "stop",
]
