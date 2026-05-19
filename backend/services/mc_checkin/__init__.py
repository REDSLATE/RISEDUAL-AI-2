"""
Alpha sidecar — MC check-in client.

Drop this file into Alpha's repo at:
    services/mc_checkin/__init__.py

Then add ONE call to Alpha's startup (FastAPI lifespan, Celery boot,
whatever bootstraps Alpha's process) and ONE periodic call.

What it does
------------
On boot (and every N minutes after), Alpha POSTs its identity stamp
to Mission Control at:

    POST {RISEDUAL_MC_URL}/api/admin/runtime/sidecar-checkin/alpha
    X-Runtime-Token: {ALPHA_MC_INGEST_TOKEN}

MC validates the stamp against the PROD doctrine, persists the latest
verdict, and renders it on the operator dashboard. If the verdict comes
back as anything other than "prod", Alpha logs LOUDLY so the operator
sees the drift on the next deploy review.

This is OBSERVABILITY ONLY. It does not gate Alpha's own execution.
The broker-side MC-receipt seal remains the lock on bad orders.

Required env vars on Alpha
--------------------------
    RISEDUAL_MC_URL              e.g. "https://mission.risedual.ai"
    ALPHA_MC_INGEST_TOKEN        the per-brain ingest token MC issued
                                 (matches MC's ALPHA_INGEST_TOKEN env)
    RISEDUAL_ENV                 "prod" on the real Alpha pod
    RISEDUAL_PLATFORM            "railway" / "render" / "fly" / etc.
    RISEDUAL_DB_NAME             Alpha's mongo DB name
    RISEDUAL_BROKER_MODE         "paper" | "live" | "dry_run"
    GIT_SHA                      git commit alpha boots from (CI sets it)
    RISEDUAL_SIDECAR_VERSION     e.g. "1.0.0" — bump on each release
    RISEDUAL_APP_NAME            "alpha"

Optional:
    RISEDUAL_MC_CHECKIN_INTERVAL_SECONDS   default 300 (5 min)

Wire-in (Alpha side)
--------------------
```python
# At Alpha's FastAPI startup or daemon boot:
from services.mc_checkin import checkin_now, start_periodic_checkin

@app.on_event("startup")
async def _mc_checkin():
    await checkin_now()                   # one synchronous boot ping
    start_periodic_checkin(app.state)     # schedules background task
```

That's it. Alpha now declares itself to MC on every boot.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

import httpx  # Alpha already depends on httpx; if not, swap for aiohttp


log = logging.getLogger("alpha.mc_checkin")


# ─────────────────────── PROD doctrine (MC-pinned) ────────────────────
#
# This dict is the CONSTITUTION. Its sha256 is the policy_hash MC
# expects to see. If Alpha ships a different dict here, MC will flag
# the check-in as `policy_drift` — meaning Alpha shipped stale doctrine
# and should redeploy with the latest mc_checkin.py.
#
# DO NOT EDIT THIS LOCALLY. Sync it from MC's
# `shared/runtime/platform_survival.py:policy_hash()` whenever MC
# changes the doctrine.
# ──────────────────────────────────────────────────────────────────────

_POLICY = {
    "sidecars_may_execute": False,
    "mc_is_source_of_truth": True,
    "roadguard_required": True,
    "broker_requires_mc_receipt": True,
    "preview_is_not_prod": True,
}


def _policy_hash() -> str:
    raw = json.dumps(_POLICY, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


# ─────────────────────── RuntimeStamp ─────────────────────────────────


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@dataclass(frozen=True)
class RuntimeStamp:
    app_name: str
    env_name: str
    git_sha: str
    platform: str
    mc_url: str
    db_name: str
    broker_mode: str
    sidecar_room: str
    sidecar_version: str
    policy_hash: str
    local_execution_authority: bool
    timestamp_ms: int

    @staticmethod
    def current() -> "RuntimeStamp":
        return RuntimeStamp(
            app_name=_env("RISEDUAL_APP_NAME", "alpha"),
            env_name=_env("RISEDUAL_ENV", "unknown"),
            git_sha=_env("GIT_SHA", "unknown"),
            platform=_env("RISEDUAL_PLATFORM", "unknown"),
            mc_url=_env("RISEDUAL_MC_URL", ""),
            db_name=_env("RISEDUAL_DB_NAME", ""),
            broker_mode=_env("RISEDUAL_BROKER_MODE", "unknown"),
            sidecar_room="alpha-room",
            sidecar_version=_env("RISEDUAL_SIDECAR_VERSION", "unknown"),
            policy_hash=_policy_hash(),
            local_execution_authority=False,  # doctrine-pinned
            timestamp_ms=int(time.time() * 1000),
        )


# ─────────────────────── HTTP client ──────────────────────────────────


def _mc_url() -> str:
    url = _env("RISEDUAL_MC_URL")
    if not url:
        raise RuntimeError(
            "RISEDUAL_MC_URL is not set on Alpha. Set it to the MC base "
            "URL (e.g. https://mission.risedual.ai) before booting."
        )
    return url.rstrip("/")


def _ingest_token() -> str:
    tok = _env("ALPHA_MC_INGEST_TOKEN")
    if not tok:
        raise RuntimeError(
            "ALPHA_MC_INGEST_TOKEN is not set on Alpha. Get it from MC's "
            "backend/.env (ALPHA_INGEST_TOKEN) and add it to Alpha's env."
        )
    return tok


async def checkin_now(timeout_seconds: float = 10.0) -> Dict[str, Any]:
    """Build the current RuntimeStamp and POST it to MC.

    Returns MC's response dict:
        {ok, runtime, verdict, errors, policy_hash_match, mc_policy_hash, note}

    Raises on transport/auth errors so the caller can decide whether to
    block boot or just log. The recommended boot-time policy is "log,
    don't block" — Alpha keeps running, but the operator sees the drift.
    """
    stamp = RuntimeStamp.current()
    payload = {"stamp": asdict(stamp)}

    url = f"{_mc_url()}/api/admin/runtime/sidecar-checkin/alpha"
    headers = {
        "X-Runtime-Token": _ingest_token(),
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        body = r.json()

    verdict = body.get("verdict", "?")
    if verdict == "prod":
        log.info(
            "mc_checkin: verdict=prod (env=%s, mc_url=%s, git=%s)",
            stamp.env_name, stamp.mc_url, stamp.git_sha,
        )
    else:
        # Loud: this is the operator's tripwire for "alpha is not where
        # I think it is". Don't downgrade to warning.
        log.error(
            "mc_checkin: verdict=%s errors=%s note=%s",
            verdict, body.get("errors"), body.get("note"),
        )

    return body


# ─────────────────────── Periodic loop ────────────────────────────────


_BACKGROUND_TASK: Optional[asyncio.Task] = None


async def _periodic_loop(interval_seconds: float) -> None:
    """Re-checks every `interval_seconds`. Failures are caught and
    logged — a flaky MC must not crash Alpha's main loop."""
    while True:
        try:
            await checkin_now()
        except Exception:  # noqa: BLE001 — defensive on every error
            log.exception("mc_checkin: periodic ping failed")
        await asyncio.sleep(interval_seconds)


def start_periodic_checkin(app_state: Any = None) -> asyncio.Task:
    """Schedule the periodic check-in as a background task. Stores the
    task on `app_state.mc_checkin_task` (if given) so a shutdown hook
    can cancel it cleanly."""
    global _BACKGROUND_TASK
    interval = float(_env("RISEDUAL_MC_CHECKIN_INTERVAL_SECONDS", "300"))
    _BACKGROUND_TASK = asyncio.create_task(_periodic_loop(interval))
    if app_state is not None:
        try:
            app_state.mc_checkin_task = _BACKGROUND_TASK
        except AttributeError:
            pass
    return _BACKGROUND_TASK


async def stop_periodic_checkin() -> None:
    """Call from Alpha's shutdown hook so the background task exits
    cleanly instead of leaking into the event-loop teardown."""
    global _BACKGROUND_TASK
    if _BACKGROUND_TASK and not _BACKGROUND_TASK.done():
        _BACKGROUND_TASK.cancel()
        try:
            await _BACKGROUND_TASK
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _BACKGROUND_TASK = None
