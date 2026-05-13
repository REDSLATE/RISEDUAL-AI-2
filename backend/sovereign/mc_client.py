"""HTTP client for Mission Control — runtime ingest endpoints only.

Verified contract (provided by MC operator on 2026-02-13):

    Auth header (single source of truth):
        X-Runtime-Token: <ALPHA_INGEST_TOKEN>      (NOT "Bearer ...")

    Endpoint 1 — Heartbeat:
        POST /api/heartbeat-ping/{runtime}
        body optional, 200 OK

    Endpoint 2 — Sovereign Contribution (periodic snapshot):
        POST /api/runtime-discussion/sovereign/contribution?runtime={runtime}
        body schema strictly enforced (see build_contribution_body).

    Endpoint 3 — Stance (per-position vote): SKIPPED in v1.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Mapping

import httpx

from .wild_adaptive_core_v2 import (
    ALLOWED_ACTIONS,
    ALLOWED_OUTCOMES,
    SUPPORTED_MODES,
)

log = logging.getLogger("sovereign.mc_client")


# ── schema bounds (mirror MC server validation) ────────────────────────
_MAX_WEIGHTS = 16
_WEIGHT_MIN, _WEIGHT_MAX = -3.0, 3.0
_LR_MIN, _LR_MAX = 0.0, 0.5
_MAX_OUTCOMES = 50


class MCContractError(ValueError):
    """Raised when a payload would violate MC's documented schema."""


class MCClientError(RuntimeError):
    """Raised on HTTP transport failure (network/timeout/non-2xx)."""


# ── pure helpers (no network, fully unit-testable) ─────────────────────
def heartbeat_url(base: str, runtime: str) -> str:
    return f"{base.rstrip('/')}/api/heartbeat-ping/{runtime}"


def contribution_url(base: str, runtime: str) -> str:
    return (
        f"{base.rstrip('/')}/api/runtime-discussion/sovereign/contribution"
        f"?runtime={runtime}"
    )


def auth_headers(token: str) -> dict[str, str]:
    """Verified header shape — no 'Bearer' prefix."""
    if not token:
        raise MCContractError("ALPHA_INGEST_TOKEN is empty")
    return {"X-Runtime-Token": token, "Content-Type": "application/json"}


def build_contribution_body(
    *,
    mode: str,
    weights: Mapping[str, float],
    learning_rate: float,
    recent_outcomes: list[Mapping[str, Any]] | None = None,
    notes: str = "",
    confidence_delta: float = 0.0,
    delta_reason: str = "",
    training_signal: bool = False,
) -> dict[str, Any]:
    """Construct + validate a contribution payload before it leaves the host.

    Raises ``MCContractError`` instead of letting MC return 422 — failing
    locally is much friendlier to the operator.
    """
    if mode not in SUPPORTED_MODES:
        raise MCContractError(f"mode must be one of {SUPPORTED_MODES}, got {mode!r}")

    # Lock #3 mirror: live_trading_enabled is HARD-CODED to False.
    # We don't even let it be a parameter — there's literally no path to True.
    body: dict[str, Any] = {
        "mode": mode,
        "live_trading_enabled": False,
    }

    # Weights validation.
    if not isinstance(weights, Mapping):
        raise MCContractError("weights must be a mapping")
    if len(weights) > _MAX_WEIGHTS:
        raise MCContractError(f"weights cap is {_MAX_WEIGHTS}, got {len(weights)}")
    w_out: dict[str, float] = {}
    for k, v in weights.items():
        if not isinstance(k, str) or not k:
            raise MCContractError(f"weight key must be non-empty string: {k!r}")
        fv = float(v)
        if not math.isfinite(fv):
            raise MCContractError(f"weight {k!r} not finite: {fv}")
        if fv < _WEIGHT_MIN or fv > _WEIGHT_MAX:
            raise MCContractError(
                f"weight {k!r}={fv} outside [{_WEIGHT_MIN}, {_WEIGHT_MAX}]"
            )
        w_out[k] = fv
    body["weights"] = w_out

    # Learning rate.
    lr = float(learning_rate)
    if not math.isfinite(lr) or lr < _LR_MIN or lr > _LR_MAX:
        raise MCContractError(f"learning_rate {lr} outside [{_LR_MIN}, {_LR_MAX}]")
    body["learning_rate"] = lr

    # Confidence delta (server CLAMPS to [-0.25, +0.25], we just must be finite).
    cd = float(confidence_delta)
    if not math.isfinite(cd):
        raise MCContractError(f"confidence_delta not finite: {cd}")
    body["confidence_delta"] = cd
    body["delta_reason"] = str(delta_reason or "")

    # Training signal (PRD mode forbids True).
    ts = bool(training_signal)
    if ts and mode == "PRD":
        raise MCContractError("training_signal=True is forbidden in PRD mode")
    body["training_signal"] = ts

    # Recent outcomes.
    outs = list(recent_outcomes or [])
    if len(outs) > _MAX_OUTCOMES:
        raise MCContractError(f"recent_outcomes cap is {_MAX_OUTCOMES}")
    validated_outs: list[dict[str, Any]] = []
    for i, rec in enumerate(outs):
        if not isinstance(rec, Mapping):
            raise MCContractError(f"outcome #{i} is not an object")
        action = rec.get("action")
        if action not in ALLOWED_ACTIONS:
            raise MCContractError(
                f"outcome #{i} action {action!r} not in {sorted(ALLOWED_ACTIONS)}"
            )
        outcome = rec.get("outcome")
        if outcome not in ALLOWED_OUTCOMES:
            raise MCContractError(
                f"outcome #{i} outcome {outcome!r} not in {sorted(ALLOWED_OUTCOMES)}"
            )
        conf = float(rec.get("confidence", 0.0))
        if not math.isfinite(conf) or conf < 0.0 or conf > 1.0:
            raise MCContractError(f"outcome #{i} confidence {conf} outside [0, 1]")
        validated_outs.append({
            "symbol": str(rec.get("symbol", "")),
            "action": str(action),
            "confidence": conf,
            "outcome": int(outcome),
            "resolved_at": str(rec.get("resolved_at") or ""),
            "notional": float(rec.get("notional", 0.0) or 0.0),
        })
    body["recent_outcomes"] = validated_outs

    body["notes"] = str(notes or "")
    return body


# ── network surface ────────────────────────────────────────────────────
class MCClient:
    """Async HTTP client. Construct once, reuse across the sidecar's loop."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        runtime: str = "alpha",
        timeout: float = 5.0,
    ) -> None:
        if not base_url:
            raise MCContractError("MC_BASE_URL is empty")
        if not token:
            raise MCContractError("ALPHA_INGEST_TOKEN is empty")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.runtime = runtime
        self._client = httpx.AsyncClient(timeout=timeout)

    @classmethod
    def from_env(cls, runtime: str = "alpha") -> "MCClient":
        base = os.environ.get("MC_BASE_URL", "").strip()
        token = os.environ.get("ALPHA_INGEST_TOKEN", "").strip()
        return cls(base_url=base, token=token, runtime=runtime)

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass

    async def heartbeat(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Best-effort liveness ping."""
        url = heartbeat_url(self.base_url, self.runtime)
        try:
            r = await self._client.post(
                url,
                json=body or {},
                headers=auth_headers(self.token),
            )
            if r.status_code >= 400:
                raise MCClientError(f"heartbeat {r.status_code}: {r.text[:200]}")
            return r.json() if r.content else {"ok": True}
        except httpx.HTTPError as e:
            raise MCClientError(f"heartbeat transport error: {e}") from e

    async def contribution(self, body: dict[str, Any]) -> dict[str, Any]:
        """Send a validated contribution payload (caller must build it)."""
        url = contribution_url(self.base_url, self.runtime)
        try:
            r = await self._client.post(url, json=body, headers=auth_headers(self.token))
            if r.status_code >= 400:
                raise MCClientError(f"contribution {r.status_code}: {r.text[:400]}")
            return r.json()
        except httpx.HTTPError as e:
            raise MCClientError(f"contribution transport error: {e}") from e
