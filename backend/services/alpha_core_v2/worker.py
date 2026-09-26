"""Alpha Core v2 scheduled worker (2026-06). Integrate via FastAPI lifespan.

Defaults OFF (``ALPHA_AUTONOMY_WORKER``). When on:
  * reconciles before every cycle (even under HALT) via AutonomyController,
  * runs Core v2 DRY (no submit) unless every live doctrine gate holds,
  * consults the EXISTING Sovereign shadow as an ADVISORY gate (blocks only
    when ``ALPHA_SOVEREIGN_ENFORCE=1`` AND the cycle is live-armed),
  * applies the exit/position-protection policy each tick (OFF unless
    ``ALPHA_V2_EXIT_POLICY=1``).

Single-worker guarantee: a cross-process file lease (``/tmp/alpha_v2_worker.lock``)
plus the caller-supplied ``order_lock`` (the route's process-wide lock). If a
lease is already held by a live PID, this worker no-ops — never two engines
submitting concurrently. Run exactly one backend process, or rely on the lease.

This module never arms live trading. Enabling the worker only starts DRY,
reconcile + advisory-shadow cycles until the operator separately sets the live
authority + arm flags.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from services.alpha_core_v2.autonomy import AutonomyController
from services.alpha_core_v2.broker import PublicBroker
from services.alpha_core_v2.config import Config
from services.alpha_core_v2.engine import CoreV2Engine
from services.alpha_core_v2.exit_policy import ExitPolicyRunner, policy_enabled
from services.alpha_core_v2.receipts import ReceiptStore
from services.alpha_core_v2.sovereign_shadow import build_gate

log = logging.getLogger(__name__)

_LOCK_PATH = Path(os.environ.get("ALPHA_V2_WORKER_LOCK") or "/tmp/alpha_v2_worker.lock")
_LEASE_TTL_S = 150  # ~5 ticks at the 30s default; stale lease is reclaimable


def worker_enabled() -> bool:
    return (os.getenv("ALPHA_AUTONOMY_WORKER") or "").lower() in ("1", "true", "yes", "on")


class WorkerLease:
    """Cross-process single-worker lease over a lockfile holding ``pid:mtime``.

    Acquire succeeds if the file is absent, stale (older than TTL), or already
    ours. ``renew`` refreshes the timestamp each tick; ``release`` removes it if
    we own it."""

    def __init__(self, path: Path = _LOCK_PATH, ttl_s: float = _LEASE_TTL_S) -> None:
        self.path = path
        self.ttl_s = ttl_s
        self.pid = os.getpid()

    def _read(self) -> Optional[tuple[int, float]]:
        try:
            raw = self.path.read_text().strip()
            pid_s, ts_s = raw.split(":", 1)
            return int(pid_s), float(ts_s)
        except (OSError, ValueError):
            return None

    def _write(self) -> None:
        self.path.write_text(f"{self.pid}:{time.time()}")

    def acquire(self) -> bool:
        cur = self._read()
        if cur is not None:
            pid, ts = cur
            fresh = (time.time() - ts) < self.ttl_s
            if fresh and pid != self.pid:
                return False
        try:
            self._write()
            return True
        except OSError:
            return False

    def renew(self) -> bool:
        cur = self._read()
        if cur is not None and cur[0] != self.pid and (time.time() - cur[1]) < self.ttl_s:
            return False  # someone else took it
        try:
            self._write()
            return True
        except OSError:
            return False

    def release(self) -> None:
        cur = self._read()
        if cur is not None and cur[0] == self.pid:
            try:
                self.path.unlink()
            except OSError:
                pass


async def run_worker(db: Any, *, stop: asyncio.Event, order_lock: asyncio.Lock,
                     feature_builder=None, interval_s: float = 30.0) -> None:
    """Call once from application lifespan. The route MUST use ``order_lock``
    too (it does — ``routes.admin_alpha_v2._live_cycle_lock``).

    ``feature_builder`` is retained for API compat but unused by the default
    (existing-shadow) gate. It only matters when ``ALPHA_SOVEREIGN_MODEL=rise``.
    """
    if interval_s < 5:
        raise ValueError("interval_s must be at least 5")
    if not worker_enabled():
        log.info("[alpha-v2-worker] disabled (ALPHA_AUTONOMY_WORKER unset) — not starting")
        return

    lease = WorkerLease()
    if not lease.acquire():
        log.warning("[alpha-v2-worker] another worker holds the lease — not starting")
        return
    log.info("[alpha-v2-worker] started pid=%s interval=%ss (DRY unless live-armed)",
             lease.pid, interval_s)

    gate_model = (os.getenv("ALPHA_SOVEREIGN_MODEL") or "shadow").strip().lower()
    enforce = (os.getenv("ALPHA_SOVEREIGN_ENFORCE") or "").strip() == "1"
    log.info("[alpha-v2-worker] gate_model=%s enforce=%s exit_policy=%s",
             gate_model, enforce, policy_enabled())

    async def engine_factory():
        cfg = Config.load()  # re-read config (and learned weights) each tick
        broker = await PublicBroker.from_db(db)
        if broker is None:
            return None
        from services.alpha_core_v2.autonomy import load_state
        live_armed = load_state(cfg).execute_live()
        return CoreV2Engine(
            broker, ReceiptStore(cfg.db_path), cfg,
            sovereign_gate=build_gate(db),
            # Enforce only when the operator armed it AND the cycle is live.
            sovereign_enforce=(enforce and live_armed),
        )

    controller = AutonomyController(engine_factory)
    try:
        while not stop.is_set():
            if not lease.renew():
                log.warning("[alpha-v2-worker] lost lease to another worker — exiting")
                return
            try:
                async with order_lock:
                    outcome = await controller.tick()
                    # Exit/position protection runs under the SAME order_lock so
                    # it can never race an entry submit.
                    if policy_enabled():
                        engine = await engine_factory()
                        if engine is not None:
                            exit_result = await ExitPolicyRunner(engine).run()
                            outcome["exit_policy"] = exit_result
                if not outcome.get("ran"):
                    log.info("[alpha-v2-worker] tick skipped: %s", outcome.get("reason"))
                else:
                    log.info("[alpha-v2-worker] cycle live=%s", outcome.get("live"))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("[alpha-v2-worker] tick failed; next tick retries")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass
    finally:
        lease.release()
        log.info("[alpha-v2-worker] stopped pid=%s", lease.pid)


__all__ = ["run_worker", "worker_enabled", "WorkerLease"]
