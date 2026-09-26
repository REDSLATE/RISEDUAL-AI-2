"""Alpha Core v2 worker — single-worker lease + OFF-by-default guarantees."""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from services.alpha_core_v2.worker import WorkerLease, run_worker, worker_enabled


def test_worker_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ALPHA_AUTONOMY_WORKER", raising=False)
    assert worker_enabled() is False


def test_lease_acquire_and_release(tmp_path):
    lock = tmp_path / "w.lock"
    lease = WorkerLease(path=lock, ttl_s=100)
    assert lease.acquire() is True
    assert lock.exists()
    assert lease.renew() is True
    lease.release()
    assert not lock.exists()


def test_fresh_lease_from_other_pid_blocks(tmp_path):
    lock = tmp_path / "w.lock"
    # Simulate another live process holding a fresh lease.
    lock.write_text(f"{os.getpid() + 12345}:{time.time()}")
    lease = WorkerLease(path=lock, ttl_s=100)
    assert lease.acquire() is False


def test_stale_lease_is_reclaimable(tmp_path):
    lock = tmp_path / "w.lock"
    lock.write_text(f"{os.getpid() + 12345}:{time.time() - 10_000}")  # stale
    lease = WorkerLease(path=lock, ttl_s=150)
    assert lease.acquire() is True  # reclaimed
    # now it's ours
    pid, _ = lease._read()
    assert pid == os.getpid()


@pytest.mark.asyncio
async def test_run_worker_noop_when_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("ALPHA_AUTONOMY_WORKER", raising=False)
    monkeypatch.setenv("ALPHA_V2_WORKER_LOCK", str(tmp_path / "w.lock"))
    stop = asyncio.Event()
    # Returns immediately (disabled) — must not hang, must not create the lock.
    await asyncio.wait_for(
        run_worker(None, stop=stop, order_lock=asyncio.Lock()), timeout=2.0)
    assert not (tmp_path / "w.lock").exists()
