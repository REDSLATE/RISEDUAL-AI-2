"""
RISEDUAL Platform Survival Layer

Purpose:
- Make PROD/preview identity explicit
- Prevent sidecars from carrying hidden execution authority
- Detect stale policy/code
- Force all trade approval through MC canonical gate
- Survive platform changes: Emergent, Railway, Render, local, VPS

This module has ZERO dependencies on the rest of the RISEDUAL
codebase — it's deliberately portable. If you fork it into a sibling
service or move hosts, this file works unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def sha256_json(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def policy_hash() -> str:
    policy = {
        "sidecars_may_execute": False,
        "mc_is_source_of_truth": True,
        "roadguard_required": True,
        "broker_requires_mc_receipt": True,
        "preview_is_not_prod": True,
    }
    return sha256_json(policy)


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
    def current(sidecar_room: str = "unknown") -> "RuntimeStamp":
        return RuntimeStamp(
            app_name=env("RISEDUAL_APP_NAME", "risedual"),
            env_name=env("RISEDUAL_ENV", env("ENV", "unknown")),
            git_sha=env("GIT_SHA", env("VERCEL_GIT_COMMIT_SHA", "unknown")),
            platform=env("RISEDUAL_PLATFORM", env("PLATFORM", "unknown")),
            mc_url=env("RISEDUAL_MC_URL", ""),
            db_name=env("RISEDUAL_DB_NAME", ""),
            broker_mode=env("RISEDUAL_BROKER_MODE", "unknown"),
            sidecar_room=sidecar_room,
            sidecar_version=env("RISEDUAL_SIDECAR_VERSION", "unknown"),
            policy_hash=policy_hash(),
            local_execution_authority=False,
            timestamp_ms=int(time.time() * 1000),
        )

    def validate_for_prod_sidecar(self) -> Dict[str, Any]:
        errors = []

        if self.env_name != "prod":
            errors.append("ENV_NOT_PROD")

        if not self.mc_url.startswith("https://mission.risedual.ai"):
            errors.append("MC_URL_NOT_PROD")

        if self.local_execution_authority is not False:
            errors.append("SIDECAR_HAS_LOCAL_EXECUTION_AUTHORITY")

        if self.git_sha in ("", "unknown"):
            errors.append("UNKNOWN_GIT_SHA")

        if self.db_name in ("", "preview", "test", "unknown"):
            errors.append("BAD_OR_UNKNOWN_DB_NAME")

        if self.broker_mode not in ("paper", "live", "dry_run"):
            errors.append("BAD_BROKER_MODE")

        return {
            "ok": not errors,
            "errors": errors,
            "stamp": asdict(self),
        }


@dataclass(frozen=True)
class IntentEnvelope:
    brain_id: str
    lane: str
    symbol: str
    direction: str
    confidence: float
    room_id: str
    runtime: RuntimeStamp

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MCExecutionReceipt:
    accepted: bool
    final_verdict: str
    reason: str
    lane: str
    symbol: str
    direction: str
    confidence: float
    mc_policy_hash: str
    issued_at_ms: int
    signature: Optional[str] = None

    def unsigned_payload(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("signature", None)
        return d

    def sign(self, secret: str) -> "MCExecutionReceipt":
        sig = hmac.new(
            secret.encode(),
            json.dumps(self.unsigned_payload(), sort_keys=True).encode(),
            hashlib.sha256,
        ).hexdigest()

        return MCExecutionReceipt(
            **{**self.unsigned_payload(), "signature": sig}
        )

    def verify(self, secret: str) -> bool:
        if not self.signature:
            return False

        expected = hmac.new(
            secret.encode(),
            json.dumps(self.unsigned_payload(), sort_keys=True).encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, self.signature)


def sidecar_build_intent(
    brain_id: str,
    lane: str,
    symbol: str,
    direction: str,
    confidence: float,
    room_id: str,
) -> Dict[str, Any]:
    """
    Sidecars call this.
    They only package intent.
    They do NOT approve execution.
    """
    stamp = RuntimeStamp.current(sidecar_room=room_id)

    envelope = IntentEnvelope(
        brain_id=brain_id,
        lane=lane,
        symbol=symbol,
        direction=direction.upper(),
        confidence=float(confidence),
        room_id=room_id,
        runtime=stamp,
    )

    return envelope.to_dict()


def mc_canonical_gate(intent: Dict[str, Any]) -> Dict[str, Any]:
    """
    MC calls this.
    This is the only place that may approve/reject before RoadGuard/broker.
    """

    runtime = intent.get("runtime", {})
    direction = str(intent.get("direction", "")).upper()
    confidence = float(intent.get("confidence", 0.0))
    lane = str(intent.get("lane", ""))
    symbol = str(intent.get("symbol", ""))

    errors = []

    if runtime.get("local_execution_authority") is not False:
        errors.append("SIDECAR_LOCAL_AUTHORITY_FORBIDDEN")

    if runtime.get("policy_hash") != policy_hash():
        errors.append("POLICY_HASH_MISMATCH")

    if direction not in {"BUY", "SELL"}:
        errors.append("DIRECTION_NOT_EXECUTABLE")

    floor = float(env(f"RISEDUAL_{lane.upper()}_CONFIDENCE_FLOOR", "0.45"))

    if confidence < floor:
        errors.append("CONFIDENCE_BELOW_FLOOR")

    if not symbol:
        errors.append("MISSING_SYMBOL")

    if lane not in {"crypto", "equity"}:
        errors.append("BAD_LANE")

    verdict = "APPROVED" if not errors else "BLOCKED"
    reason = "MC_CANONICAL_GATE_APPROVED" if not errors else errors[0]

    receipt = MCExecutionReceipt(
        accepted=not errors,
        final_verdict=verdict,
        reason=reason,
        lane=lane,
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        mc_policy_hash=policy_hash(),
        issued_at_ms=int(time.time() * 1000),
    )

    secret = env("RISEDUAL_MC_RECEIPT_SECRET", "")
    signed = receipt.sign(secret) if secret else receipt

    return {
        "accepted": signed.accepted,
        "final_verdict": signed.final_verdict,
        "reason": signed.reason,
        "errors": errors,
        "receipt": asdict(signed),
    }


def broker_verify_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """
    Broker adapter calls this before any paper/live order.
    Broker refuses orders without MC receipt.
    """

    secret = env("RISEDUAL_MC_RECEIPT_SECRET", "")

    if not secret:
        return {"ok": False, "reason": "MISSING_RECEIPT_SECRET"}

    obj = MCExecutionReceipt(**receipt)

    if not obj.verify(secret):
        return {"ok": False, "reason": "BAD_MC_RECEIPT_SIGNATURE"}

    if not obj.accepted:
        return {"ok": False, "reason": obj.reason}

    return {
        "ok": True,
        "reason": "VALID_MC_RECEIPT",
        "lane": obj.lane,
        "symbol": obj.symbol,
        "direction": obj.direction,
    }
