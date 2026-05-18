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


def intents_url(base: str) -> str:
    """Intent submission endpoint (MC's `shared_intents` collection)."""
    return f"{base.rstrip('/')}/api/intents"


# Honesty-receipt fields accepted by MC's IntentIn schema (2026-05-15).
# All optional; brains that don't ship them forfeit their audit trail
# (and produce `total_intents=N, blocked_directional=0` on MC's
# /api/admin/intents/honesty endpoint — the exact symptom that
# prompted this patch). The list is kept here, not in
# build_intent_body, because the validation rules differ per field
# and inlining them keeps the validator readable.
_HONESTY_ACTION_FIELDS = ("raw_action", "market_decision", "display_action")
_HONESTY_EXEC_VALUES = frozenset({"ALLOW", "BLOCK", "SIZE_DOWN", "OBSERVE_ONLY"})
_HONESTY_WEIGHT_FIELDS = (
    "strategist_weight", "auditor_weight", "commander_weight",
    "regime_weight", "memory_weight",
)


def build_intent_body(
    *,
    symbol: str,
    side: str,
    qty: float,
    confidence: float,
    notes: str = "",
    # ── honesty receipt (all optional, MC server is additive-safe) ──
    raw_action: str | None = None,
    raw_confidence: float | None = None,
    market_decision: str | None = None,
    execution_decision: str | None = None,
    display_action: str | None = None,
    hold_reason: str | None = None,
    blocked_by: Iterable[str] | None = None,
    would_have_traded_without_gates: bool | None = None,
    pre_weight_confidence: float | None = None,
    post_weight_confidence: float | None = None,
    council_penalty: float | None = None,
    strategist_weight: float | None = None,
    auditor_weight: float | None = None,
    commander_weight: float | None = None,
    regime_weight: float | None = None,
    memory_weight: float | None = None,
) -> dict[str, Any]:
    """Construct + validate an intent payload before it leaves the host.

    Required core fields mirror MC's ``IntentIn`` schema. Honesty
    receipt fields are optional but doctrinally required (2026-05-15):
    a brain that omits them produces the silent-state failure mode
    (blocked trades indistinguishable from HOLD opinions) that poisons
    Hypothesis recall and drifts the council neutral. See
    ``services.confidence_weighting`` for the math behind the bounded
    penalty and dynamic weights.

    Raises ``MCContractError`` on local validation failure — we'd
    rather fail fast than discover a typo in MC's 422 response.
    """
    if not symbol or not isinstance(symbol, str):
        raise MCContractError("intent symbol must be a non-empty string")
    side_u = (side or "").upper()
    if side_u not in ALLOWED_ACTIONS:
        raise MCContractError(
            f"intent side {side!r} not in {sorted(ALLOWED_ACTIONS)}"
        )
    qf = float(qty)
    if not math.isfinite(qf) or qf <= 0:
        raise MCContractError(f"intent qty must be > 0, got {qf}")
    cf = float(confidence)
    if not math.isfinite(cf) or cf < 0.0 or cf > 1.0:
        raise MCContractError(f"intent confidence {cf} outside [0, 1]")

    body: dict[str, Any] = {
        "symbol": symbol.upper(),
        "side": side_u,
        "qty": qf,
        "confidence": cf,
        "notes": str(notes or ""),
    }

    # Action-domain honesty fields — must be subset of ALLOWED_ACTIONS
    # if present (BUY / SELL / HOLD / SHORT / COVER). Validation is
    # symmetric across raw_action / market_decision / display_action
    # because all three live in the same action space.
    for fname, fval in (
        ("raw_action", raw_action),
        ("market_decision", market_decision),
        ("display_action", display_action),
    ):
        if fval is None:
            continue
        fv = str(fval).upper()
        if fv not in ALLOWED_ACTIONS:
            raise MCContractError(
                f"intent {fname} {fval!r} not in {sorted(ALLOWED_ACTIONS)}"
            )
        body[fname] = fv

    # execution_decision is its own enum — explicitly NOT a directional
    # action. ALLOW/BLOCK/SIZE_DOWN/OBSERVE_ONLY only.
    if execution_decision is not None:
        ed = str(execution_decision).upper()
        if ed not in _HONESTY_EXEC_VALUES:
            raise MCContractError(
                f"intent execution_decision {execution_decision!r} not in "
                f"{sorted(_HONESTY_EXEC_VALUES)}"
            )
        body["execution_decision"] = ed

    # Bounded scalars [0, 1] — raw_confidence + the two weight stages.
    for fname, fval in (
        ("raw_confidence", raw_confidence),
        ("pre_weight_confidence", pre_weight_confidence),
        ("post_weight_confidence", post_weight_confidence),
    ):
        if fval is None:
            continue
        fv2 = float(fval)
        if not math.isfinite(fv2) or fv2 < 0.0 or fv2 > 1.0:
            raise MCContractError(
                f"intent {fname} {fv2} outside [0, 1]"
            )
        body[fname] = fv2

    # council_penalty is a signed delta — typically negative when
    # disagreement bites. Bounded to [-1, 1] defensively.
    if council_penalty is not None:
        cp = float(council_penalty)
        if not math.isfinite(cp) or cp < -1.0 or cp > 1.0:
            raise MCContractError(
                f"intent council_penalty {cp} outside [-1, 1]"
            )
        body["council_penalty"] = cp

    # Dynamic per-source weights — bounded [0, 3] (matches the
    # confidence_weighting clamp envelope). All five travel together
    # or not at all in production; we tolerate a partial set during
    # rollout so brains can start emitting incrementally.
    for fname in _HONESTY_WEIGHT_FIELDS:
        fval = locals()[fname]
        if fval is None:
            continue
        wv = float(fval)
        if not math.isfinite(wv) or wv < 0.0 or wv > 3.0:
            raise MCContractError(f"intent {fname} {wv} outside [0, 3]")
        body[fname] = wv

    if hold_reason is not None:
        body["hold_reason"] = str(hold_reason)
    if blocked_by is not None:
        body["blocked_by"] = [str(b) for b in blocked_by]
    if would_have_traded_without_gates is not None:
        body["would_have_traded_without_gates"] = bool(
            would_have_traded_without_gates,
        )

    return body


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

    # Lock #3 mirror: **2026-05-17 Operator Override.** Under
    # Doctrine V3 the local pod doesn't execute, but the brain
    # *advertises live trading capability* so MC's executor seat
    # knows this brain is open for business. MC remains the
    # execution authority.
    body: dict[str, Any] = {
        "mode": mode,
        "live_trading_enabled": True,
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

    def post_intent(self, **kwargs: Any) -> dict[str, Any]:
        """POST an intent (with honesty receipt) to MC.

        Thin wrapper over :func:`build_intent_body`. The brain side
        constructs the receipt from ``_weighted_consensus`` output
        and the local execution gate's verdict; this method only
        validates the shape and ships it. All honesty fields are
        keyword-only and optional — a brain in the middle of the
        rollout can ship the core 4 (symbol/side/qty/confidence)
        without the receipt, and the validator will accept it.

        See ``services.confidence_weighting`` doctrine module for the
        bounded penalty + dynamic weight math, and
        ``services.multi_model_hypothesis_service._weighted_consensus``
        for the canonical receipt assembly.
        """
        body = build_intent_body(**kwargs)
        return self._post(intents_url(self.base_url), body)
