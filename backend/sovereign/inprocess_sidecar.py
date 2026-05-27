"""In-process Sovereign Sidecar (2026-02-23, prod-deploy fix).

Background
----------
The supervisor-managed external sidecar (``alpha-sidecar.conf``)
exists on the *preview* pod at ``/etc/supervisor/conf.d/`` but is
NOT part of Emergent's deploy image. So production has been
running with **no sidecar process at all** — zero contributions to
MC since deploy, which is why MC's age-based classifier showed
`HEARTBEAT ONLY · 54987s ago` on the dashboard the operator
screenshotted.

This module ships the same contribution loop in-process — spawned
as a FastAPI lifespan task — so a vanilla Emergent deploy of
``/app`` carries it for free.

Why not the deleted ``services/mc_sidecar._contribution_loop``?
---------------------------------------------------------------
That loop posted a HARDCODED placeholder body and raced the
supervisor sidecar on the outcome-inbox drain. We deleted it on
2026-02-23. This new module is different in three doctrine-
relevant ways:

1. It reuses the canonical ``sovereign.sidecar.SovereignSidecar``
   class — the SAME class the supervisor process runs — so
   payload shape, lineage stamp, weights, outcome-inbox drain,
   and empty-payload refusal are all identical.
2. It honours a **lockfile** at ``/tmp/alpha-sidecar.lock``. If
   the supervisor sidecar is also running (preview env), the
   lockfile is present and recent, and the in-process loop
   short-circuits — no race, no double-POST.
3. It only spawns when ``ALPHA_INPROCESS_SIDECAR_ENABLED=1`` so
   preview (which has the supervisor process) stays exactly the
   same; the operator flips this env in prod after the next
   redeploy.

Lockfile contract
-----------------
* Supervisor sidecar SHOULD touch ``/tmp/alpha_alive`` after every
  tick (already wired since 2026-05-14).
* On startup we check ``/tmp/alpha_alive``. If its mtime is
  younger than ``ALPHA_SIDECAR_LOCKFILE_MAX_AGE_S`` (default 180s
  = ~3 tick cycles), the supervisor wins; we don't spawn.
* Otherwise we spawn AND start touching ``/tmp/alpha_alive`` from
  our own loop. If the supervisor sidecar comes back later, the
  next deploy is the natural place to retire one of them — the
  doubly-running edge case is brief.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_DEFAULT_LIVENESS_FILE = "/tmp/alpha_alive"
_DEFAULT_LOCK_MAX_AGE_S = 180  # 3× the 60s tick interval
_DEFAULT_INTERVAL_S = 60

# Module-scoped task handle so the lifespan can cancel cleanly.
_sidecar_task: Optional[asyncio.Task] = None


def _is_enabled() -> bool:
    """Default OFF. Operator flips ``ALPHA_INPROCESS_SIDECAR_ENABLED=1``
    in prod env after redeploy. Preview stays OFF so the supervisor
    sidecar continues to own the wire there."""
    v = (os.environ.get("ALPHA_INPROCESS_SIDECAR_ENABLED", "") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _lockfile_path() -> Path:
    return Path(
        os.environ.get("SOVEREIGN_LIVENESS_FILE") or _DEFAULT_LIVENESS_FILE
    )


def _lock_max_age_s() -> int:
    try:
        return int(
            (os.environ.get("ALPHA_SIDECAR_LOCKFILE_MAX_AGE_S", "") or "").strip()
            or _DEFAULT_LOCK_MAX_AGE_S
        )
    except (TypeError, ValueError):
        return _DEFAULT_LOCK_MAX_AGE_S


def is_supervisor_sidecar_winning() -> bool:
    """Lockfile guard. If ``/tmp/alpha_alive`` mtime is fresher than
    the threshold, the supervisor process is alive and the
    in-process loop must no-op."""
    fp = _lockfile_path()
    try:
        if not fp.exists():
            return False
        age = time.time() - fp.stat().st_mtime
        return age < _lock_max_age_s()
    except OSError:
        return False


def _build_sidecar() -> Optional[object]:
    """Construct ``SovereignSidecar`` from env. Returns None if any
    required env is missing (we fail-soft so a misconfigured prod
    pod doesn't crash the backend's startup)."""
    brain = (os.environ.get("ALPHA_BRAIN_NAME") or "alpha").strip()
    mc_url = (os.environ.get("MC_BASE_URL") or "").strip()
    token = (os.environ.get(f"{brain.upper()}_INGEST_TOKEN") or "").strip()
    if not mc_url or not token:
        logger.warning(
            "[alpha_inprocess_sidecar] missing MC_BASE_URL or "
            "%s_INGEST_TOKEN — not spawning",
            brain.upper(),
        )
        return None

    state_path_env = os.environ.get("SOVEREIGN_STATE_PATH")
    state_path: Optional[Path] = (
        Path(state_path_env) if state_path_env else None
    )

    # Lazy import so a backend-only deploy (no brain code) doesn't
    # crash at startup with an ImportError on this module.
    try:
        # Local import path matches what the supervisor sidecar uses
        # when run as ``python -m sovereign.sidecar``.
        import sys
        sov_dir = Path(__file__).parent
        if str(sov_dir) not in sys.path:
            sys.path.insert(0, str(sov_dir))
        from sidecar import SovereignSidecar  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[alpha_inprocess_sidecar] import failed: %s", exc,
        )
        return None

    symbols_env = (os.environ.get("ALPHA_SIDECAR_SYMBOLS") or
                   "BTC/USD,ETH/USD,SOL/USD").strip()
    symbols = [s.strip() for s in symbols_env.split(",") if s.strip()]

    mode = (os.environ.get("ALPHA_SIDECAR_MODE") or "DTD").strip()

    return SovereignSidecar(
        brain=brain, mode=mode, mc_base_url=mc_url,
        runtime_token=token, symbols=symbols, state_path=state_path,
    )


def _interval_s() -> int:
    try:
        return int(
            (os.environ.get("ALPHA_SIDECAR_INTERVAL_S", "") or "").strip()
            or _DEFAULT_INTERVAL_S
        )
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL_S


async def _sidecar_loop() -> None:
    """The in-process contribution loop. Runs tick() forever at
    ``interval_seconds`` cadence. Tick failures are logged but
    never raise — the loop is the SLO for MC's `sv_iso` freshness
    classifier."""
    sidecar = _build_sidecar()
    if sidecar is None:
        logger.warning(
            "[alpha_inprocess_sidecar] sidecar build failed — loop exits",
        )
        return

    interval = _interval_s()
    logger.info(
        "[alpha_inprocess_sidecar] started: brain=%s mode=%s interval=%ds "
        "(in-process, ships with deploy image)",
        getattr(sidecar, "brain", "?"),
        getattr(getattr(sidecar, "state", None), "mode", "?"),
        interval,
    )

    while True:
        try:
            # If the supervisor process came up after we did, hand
            # control back to it. (Won't happen in vanilla deploy
            # but the cost of checking is zero.)
            if is_supervisor_sidecar_winning():
                logger.info(
                    "[alpha_inprocess_sidecar] supervisor sidecar now "
                    "owns the lockfile — yielding for this tick",
                )
            else:
                # ``SovereignSidecar.tick`` is synchronous (uses
                # httpx.Client). Run in a thread so the event loop
                # stays responsive for the FastAPI app.
                await asyncio.to_thread(sidecar.tick)
        except asyncio.CancelledError:
            logger.info("[alpha_inprocess_sidecar] cancelled; shutting down")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[alpha_inprocess_sidecar] tick failed (non-fatal): %s", exc,
            )
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("[alpha_inprocess_sidecar] cancelled mid-sleep")
            raise


async def start(app=None) -> dict:
    """FastAPI lifespan / startup hook. Idempotent.

    Returns a tiny status dict so server.py's startup logger can
    emit a deterministic line. Doctrine: NEVER raise from here.
    A backend that can't spawn the in-process sidecar must still
    serve API requests; the operator can fix env and bounce the
    pod separately.
    """
    global _sidecar_task

    if not _is_enabled():
        return {"started": False, "reason": "disabled"}

    if is_supervisor_sidecar_winning():
        logger.info(
            "[alpha_inprocess_sidecar] supervisor sidecar already alive "
            "(lockfile fresh) — not spawning",
        )
        return {"started": False, "reason": "supervisor_present"}

    if _sidecar_task is not None and not _sidecar_task.done():
        return {"started": False, "reason": "already_running"}

    _sidecar_task = asyncio.create_task(_sidecar_loop())
    return {"started": True, "reason": "spawned"}


async def stop() -> dict:
    """Cancel + await the loop. Idempotent."""
    global _sidecar_task
    if _sidecar_task is None or _sidecar_task.done():
        return {"stopped": False, "reason": "not_running"}
    _sidecar_task.cancel()
    try:
        await _sidecar_task
    except (asyncio.CancelledError, Exception):
        pass
    _sidecar_task = None
    return {"stopped": True}


def status() -> dict:
    """Snapshot for the admin endpoint."""
    t = _sidecar_task
    return {
        "enabled": _is_enabled(),
        "task_alive": bool(t and not t.done()),
        "supervisor_present": is_supervisor_sidecar_winning(),
        "interval_s": _interval_s(),
        "lockfile": str(_lockfile_path()),
        "lockfile_max_age_s": _lock_max_age_s(),
    }


__all__ = [
    "start", "stop", "status",
    "is_supervisor_sidecar_winning",
    "_is_enabled",
]
