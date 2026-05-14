"""HTTP client for Mission Control — runtime ingest endpoints.

Verified contract (provided by MC operator on 2026-02-13):

    Auth header (single source of truth):
        X-Runtime-Token: <ALPHA_INGEST_TOKEN>      (NOT "Bearer ...")

    Endpoint 1 — Heartbeat:
        POST /api/heartbeat-ping/{brain}
        body optional; 200 OK

    Endpoint 2 — Sovereign Contribution (periodic snapshot):
        POST /api/runtime-discussion/sovereign/contribution?runtime={brain}
        strict schema; 422 on violation; we validate locally to fail fast.

    Endpoint 3 — Stance (per-position vote):
        POST /api/runtime-discussion/positions/{position_id}/stance?runtime={brain}
        body: {stance, confidence, notes, memory_sources, confidence_origin}

Module split:
    - Pure helpers (URL builders, header builder, body builder) live at
      module level and are unit-testable without any network.
    - `MCClient` is a small sync wrapper that uses those helpers.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Iterable, Mapping

import httpx

try:
    from .wild_adaptive_core_v2 import (
        ALLOWED_ACTIONS,
        ALLOWED_OUTCOMES,
        SUPPORTED_MODES,
    )
except ImportError:  # bare import via sidecar's sys.path tweak
    from wild_adaptive_core_v2 import (  # type: ignore[no-redef]
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

_ALLOWED_STANCES: frozenset[str] = frozenset({"long", "short", "abstain"})


class MCContractError(ValueError):
    """Raised when a payload would violate MC's documented schema."""


class MCClientError(RuntimeError):
    """Raised on HTTP transport failure (network/timeout/non-2xx)."""


# ── pure helpers ───────────────────────────────────────────────────────
def heartbeat_url(base: str, runtime: str) -> str:
    return f"{base.rstrip('/')}/api/heartbeat-ping/{runtime}"


def contribution_url(base: str, runtime: str) -> str:
    return (
        f"{base.rstrip('/')}/api/runtime-discussion/sovereign/contribution"
        f"?runtime={runtime}"
    )


def stance_url(base: str, position_id: str, runtime: str) -> str:
    return (
        f"{base.rstrip('/')}/api/runtime-discussion/positions/{position_id}/stance"
        f"?runtime={runtime}"
    )


def auth_headers(token: str) -> dict[str, str]:
    """Verified header shape — no 'Bearer' prefix."""
    if not token:
        raise MCContractError("runtime token is empty")
    return {"X-Runtime-Token": token, "Content-Type": "application/json"}


def build_contribution_body(
    *,
    mode: str,
    weights: Mapping[str, float],
    learning_rate: float,
    recent_outcomes: Iterable[Mapping[str, Any]] | None = None,
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

    lr = float(learning_rate)
    if not math.isfinite(lr) or lr < _LR_MIN or lr > _LR_MAX:
        raise MCContractError(f"learning_rate {lr} outside [{_LR_MIN}, {_LR_MAX}]")
    body["learning_rate"] = lr

    cd = float(confidence_delta)
    if not math.isfinite(cd):
        raise MCContractError(f"confidence_delta not finite: {cd}")
    body["confidence_delta"] = cd
    body["delta_reason"] = str(delta_reason or "")

    ts = bool(training_signal)
    if ts and mode == "PRD":
        raise MCContractError("training_signal=True is forbidden in PRD mode")
    body["training_signal"] = ts

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


def build_stance_body(
    *,
    stance: str,
    confidence: float,
    notes: str = "",
    memory_sources: Iterable[str] | None = None,
    confidence_origin: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Construct + validate a per-position stance payload."""
    if stance not in _ALLOWED_STANCES:
        raise MCContractError(
            f"stance {stance!r} not in {sorted(_ALLOWED_STANCES)} "
            "(translate from BUY/SELL/HOLD via map_action_to_stance)"
        )
    c = float(confidence)
    if not math.isfinite(c) or c < 0.0 or c > 1.0:
        raise MCContractError(f"confidence {c} outside [0, 1]")
    body: dict[str, Any] = {
        "stance": stance,
        "confidence": c,
        "notes": str(notes or ""),
        "memory_sources": [str(s) for s in (memory_sources or [])],
        "confidence_origin": {
            str(k): float(v) for k, v in (confidence_origin or {}).items()
            if math.isfinite(float(v))
        },
    }
    return body


# ── sync HTTP wrapper ──────────────────────────────────────────────────
class MCClient:
    """Sync HTTP client. Construct once, reuse across the sidecar's loop."""

    def __init__(
        self,
        *,
        base_url: str,
        brain: str,
        runtime_token: str,
        timeout: float = 5.0,
    ) -> None:
        if not base_url:
            raise MCContractError("MC base_url is empty")
        if not runtime_token:
            raise MCContractError("runtime_token is empty")
        if not brain:
            raise MCContractError("brain is empty")
        self.base_url = base_url.rstrip("/")
        self.brain = brain
        self.token = runtime_token
        # ── 2026-05-14 hardening ───────────────────────────────────────
        # The default httpx.Client(timeout=5.0) collapses all phases into
        # one number AND keeps connections alive across the LB. When MC's
        # upstream rotates a pod, the cached socket goes half-open and
        # the next POST blocks indefinitely in TLS — the single-number
        # timeout doesn't always trip on a `pool` acquire stall. We
        # explicitly bound every phase AND disable keep-alive: the
        # sidecar makes ~1 req/min so the cost of a fresh handshake each
        # tick is negligible compared to the cost of a silent freeze.
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=3.0, read=timeout, write=5.0, pool=2.0),
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=4),
        )

    @classmethod
    def from_env(cls, brain: str = "alpha") -> "MCClient":
        base = os.environ.get("MC_BASE_URL", "").strip()
        token = os.environ.get(f"{brain.upper()}_INGEST_TOKEN", "").strip()
        return cls(base_url=base, brain=brain, runtime_token=token)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    # ── network calls (raise MCClientError on non-2xx / transport fail) ──
    def _post(self, url: str, body: Mapping[str, Any] | None) -> dict[str, Any]:
        try:
            r = self._client.post(url, json=dict(body or {}), headers=auth_headers(self.token))
            if r.status_code >= 400:
                raise MCClientError(f"POST {url} → {r.status_code}: {r.text[:400]}")
            return r.json() if r.content else {"ok": True}
        except httpx.HTTPError as e:
            raise MCClientError(f"transport error {url}: {e}") from e

    def heartbeat(self, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._post(heartbeat_url(self.base_url, self.brain), body)

    def post_contribution(
        self,
        *,
        mode: str,
        weights: Mapping[str, float],
        learning_rate: float,
        recent_outcomes: Iterable[Mapping[str, Any]] | None = None,
        notes: str = "",
        confidence_delta: float = 0.0,
        delta_reason: str = "",
        training_signal: bool = False,
    ) -> dict[str, Any]:
        body = build_contribution_body(
            mode=mode,
            weights=weights,
            learning_rate=learning_rate,
            recent_outcomes=recent_outcomes,
            notes=notes,
            confidence_delta=confidence_delta,
            delta_reason=delta_reason,
            training_signal=training_signal,
        )
        return self._post(contribution_url(self.base_url, self.brain), body)

    def post_stance(
        self,
        *,
        position_id: str,
        stance: str,
        confidence: float,
        notes: str = "",
        memory_sources: Iterable[str] | None = None,
        confidence_origin: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        if not position_id:
            raise MCContractError("position_id is empty")
        body = build_stance_body(
            stance=stance,
            confidence=confidence,
            notes=notes,
            memory_sources=memory_sources,
            confidence_origin=confidence_origin,
        )
        return self._post(stance_url(self.base_url, position_id, self.brain), body)
