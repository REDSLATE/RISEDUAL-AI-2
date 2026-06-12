"""Sidecar client for writing observation data to the RISEDUAL monorepo.
Per doctrine: this is the ONLY file in this runtime that knows about the
monorepo's shared collections. Decision logic stays out of here.

Failures NEVER raise — the monorepo being down must not take down this runtime.
The sidecar is fire-and-forget; every emit is best-effort. If the env vars
are not set or the host is unreachable, calls are silently skipped.
"""
from __future__ import annotations

import asyncio
import os
import logging
from typing import Awaitable, Coroutine

import httpx

log = logging.getLogger("risedual.monorepo_client")


def _enabled() -> bool:
    """Sidecar is enabled only when all 3 env vars are set AND the
    operator has not flipped the kill switch.

    2026-06-09 MC2 severance: also forced OFF when
    ``RISEDUAL_STANDALONE_MODE=1`` — this client talks to
    ``mission.risedual.ai``'s runtime-discussion endpoints, which
    are exactly what standalone mode disconnects from.
    """
    try:
        from services.mc2 import is_standalone
        if is_standalone():
            return False
    except Exception:  # noqa: BLE001
        pass
    if os.environ.get("MONOREPO_SIDECAR_ENABLED", "true").lower() == "false":
        return False
    return bool(
        os.environ.get("MONOREPO_BASE_URL")
        and os.environ.get("MONOREPO_INGEST_TOKEN")
        and os.environ.get("RUNTIME_NAME")
    )


def _base() -> str:
    return os.environ["MONOREPO_BASE_URL"].rstrip("/")


def _token() -> str:
    return os.environ["MONOREPO_INGEST_TOKEN"]


def _runtime() -> str:
    return os.environ["RUNTIME_NAME"]


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=5.0)
    return _client


async def aclose() -> None:
    """Close the underlying httpx client. Idempotent."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # noqa: BLE001
            pass
        _client = None


def fire_and_forget(coro: Coroutine | Awaitable) -> None:
    """Schedule ``coro`` on the running event loop without awaiting it.
    If no loop is running (sync caller without an active loop), the
    coroutine is closed without execution — sync callers from request
    handlers are expected to be on a running loop already. NEVER raises.
    """
    if coro is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread — drop the coro cleanly.
        try:
            coro.close()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        loop.create_task(coro)
    except Exception as e:  # noqa: BLE001
        log.debug("monorepo fire_and_forget schedule failed: %s", e)
        try:
            coro.close()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


async def _post(path: str, body: dict) -> dict:
    if not _enabled():
        return {"ok": False, "error": "sidecar_disabled"}
    body = {"runtime": _runtime(), **body}
    try:
        r = await _get_client().post(
            f"{_base()}/api/ingest/{path}",
            json=body,
            headers={"X-Runtime-Token": _token()},
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.debug("monorepo ingest %s failed: %s", path, e)
        return {"ok": False, "error": str(e)}


async def emit_receipt(action: str, intent: dict, executed: bool = False) -> dict:
    """Mirror an ADL receipt. Server forces executed=False unless monorepo's
    BROKER_LIVE_ORDER_ENABLED=true."""
    return await _post("receipts", {"action": action, "intent": intent or {}, "executed": bool(executed)})


async def emit_memory_label(label: str, reason: str = "", payload_summary: str = "") -> dict:
    """label must be 'safe' | 'review' | 'quarantine'."""
    return await _post("memory-labels", {
        "label": label, "reason": reason or "", "payload_summary": payload_summary or "",
    })


async def register_calibrator(name: str, version: str, method: str, fit_at: str | None = None) -> dict:
    """Idempotent. Call after every refit."""
    return await _post("calibrators", {
        "name": name, "version": version, "method": method, "fit_at": fit_at,
    })


async def register_artifact(artifact: str, version: str, sha: str, registered_at: str | None = None) -> dict:
    """Idempotent. Call at startup + on retrain."""
    return await _post("artifacts", {
        "artifact": artifact, "version": version, "sha": sha, "registered_at": registered_at,
    })


async def heartbeat(status: str = "ok", detail: dict | None = None) -> dict:
    """Liveness ping. Call every 30-60s in a background task."""
    return await _post("heartbeat", {"status": status, "detail": detail or {}})


# ── Higher-level mirrors (sync entry points) ────────────────────────


def mirror_calibration_artifact(
    *,
    artifact_path,
    version: str,
    method: str,
    fit_at: str | None = None,
    name: str = "chevelle_isotonic",
    artifact_kind: str = "calibrator",
) -> None:
    """Fire-and-forget: sha-hash the artifact and register it +
    its calibrator entry with the monorepo. Sync — schedules the
    coroutine on the running loop. NEVER raises."""
    try:
        import hashlib
        from pathlib import Path as _Path
        p = _Path(artifact_path)
        try:
            sha = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""
        except Exception:  # noqa: BLE001
            sha = ""

        async def _both():
            await register_calibrator(
                name=name, version=version, method=method, fit_at=fit_at,
            )
            await register_artifact(
                artifact=artifact_kind, version=version, sha=sha,
                registered_at=fit_at,
            )

        fire_and_forget(_both())
    except Exception as e:  # noqa: BLE001
        log.debug("mirror_calibration_artifact skipped: %s", e)



# ── Discussion layer (cross-brain opinions; writes via /api/ingest/opinion,
# reads via /api/runtime-discussion/*; different routers, same auth) ──


async def _get(path: str, params: dict) -> dict:
    """Best-effort GET. Returns ``{}``-shaped fallback on any failure."""
    if not _enabled():
        return {"items": [], "count": 0, "error": "sidecar_disabled"}
    try:
        r = await _get_client().get(
            f"{_base()}{path}",
            params=params,
            headers={"X-Runtime-Token": _token()},
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("monorepo GET %s failed: %s", path, e)
        return {"items": [], "count": 0, "error": str(e)}


async def post_opinion(
    topic: str,
    stance: str,
    body: str,
    *,
    confidence: float = 0.5,
    evidence: dict | None = None,
    in_reply_to: str | None = None,
) -> dict:
    """Post this brain's opinion to the cross-brain discussion layer.

    Doctrine: ``may_execute`` is always False — opinions are observations,
    never executions. ``evidence`` carries references, never raw model state.
    """
    return await _post("opinion", {
        "topic": str(topic),
        "stance": str(stance),
        "confidence": float(confidence),
        "body": str(body),
        "evidence": evidence or {},
        "in_reply_to": in_reply_to,
        "may_execute": False,
    })


async def read_opinions(
    *,
    runtime: str | None = None,
    topic: str | None = None,
    symbol: str | None = None,
    thread: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> dict:
    """Pull recent opinions from any brain. Best-effort; returns empty on error."""
    if not _enabled():
        return {"items": [], "count": 0, "error": "sidecar_disabled"}
    params: dict[str, str] = {"caller": _runtime(), "limit": str(int(limit))}
    for k, v in (
        ("runtime", runtime), ("topic", topic), ("symbol", symbol),
        ("thread", thread), ("since", since),
    ):
        if v:
            params[k] = str(v)
    return await _get("/api/runtime-discussion/opinions", params)


async def read_roles_manifest() -> dict:
    """Pull the live roster of connected brains + their declared roles."""
    if not _enabled():
        return {"items": [], "count": 0, "error": "sidecar_disabled"}
    return await _get(
        "/api/runtime-discussion/roles-manifest",
        {"caller": _runtime()},
    )


async def read_my_scorecard(since: str | None = None) -> dict:
    """Pull this brain's accuracy/contribution scorecard from MC."""
    if not _enabled():
        return {"runtime": "", "summary": {}, "error": "sidecar_disabled"}
    params: dict[str, str] = {"caller": _runtime()}
    if since:
        params["since"] = str(since)
    try:
        r = await _get_client().get(
            f"{_base()}/api/runtime-discussion/scorecard",
            params=params,
            headers={"X-Runtime-Token": _token()},
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("scorecard read failed: %s", e)
        return {"runtime": _runtime(), "summary": {}, "error": str(e)}
