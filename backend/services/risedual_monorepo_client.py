"""Sidecar client for writing observation data to the RISEDUAL monorepo.
Per doctrine: this is the ONLY file in this runtime that knows about the
monorepo's shared collections. Decision logic stays out of here.

Failures NEVER raise — the monorepo being down must not take down this runtime.
"""
from __future__ import annotations

import os
import logging
import httpx

log = logging.getLogger("risedual.monorepo_client")


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


async def _post(path: str, body: dict) -> dict:
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
        log.warning("monorepo ingest %s failed: %s", path, e)
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
