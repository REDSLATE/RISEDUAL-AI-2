"""Promotion Bridge — the only PRD → DTD path.

Implements §5 of ``RISEDUAL_DUAL_STACK_SPEC.md``. Every bridge:

* is registered in code (so deployment is auditable via git)
* is **inactive by default** — must be explicitly activated with an
  approval token
* can only inject **calibration parameters** within pre-set bounds
* never emits a directional action or vetoes a trade
* logs every activation + revocation to ``bridge_activations``
  (immutable audit trail)
* is revocable in one call

DTD callers consume bridge output through ``get_calibration(name)``.
A ``None`` return means "no bridge active" — DTD code MUST default to
the unmodified parameter when the return is ``None``. Bridges never
have an "off" value that means "do something different"; absence of a
bridge value is the safe default.

No bridges ship registered. The first real bridge will be added in a
follow-up PR with its own activation playbook entry.
"""
from __future__ import annotations

__domain__ = "BRIDGE"

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Bridge spec ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class BridgeSpec:
    """Static, code-pinned definition of a bridge.

    Frozen by design: a bridge's activation criteria, output target,
    and bounds cannot be edited at runtime. To change a bridge,
    register a new versioned spec (``bridge_vN+1``).
    """
    name: str
    version: str
    output_target: str  # named calibration param this bridge can set
    output_bounds: tuple[float, float]  # (lo, hi) hard clamp
    min_samples: int
    oos_window_days: int
    regression_threshold: float
    description: str = ""


# ── Registry ─────────────────────────────────────────────────────────


_REGISTRY: dict[str, BridgeSpec] = {}
_ACTIVE: dict[str, dict[str, Any]] = {}  # name → {"value": float, "activated_at": iso, "actor": str, "metadata": {...}}
_db: Any = None


def set_db(db: Any) -> None:
    global _db
    _db = db


def register(spec: BridgeSpec) -> None:
    """Register a bridge at module-import time. Idempotent on
    (name, version); a different version with the same name is a
    deliberate replacement."""
    if spec.output_bounds[0] > spec.output_bounds[1]:
        raise ValueError(f"bridge {spec.name}: bounds inverted")
    _REGISTRY[spec.name] = spec


def list_specs() -> list[dict]:
    return [
        {
            "name": s.name, "version": s.version,
            "output_target": s.output_target,
            "output_bounds": list(s.output_bounds),
            "min_samples": s.min_samples,
            "oos_window_days": s.oos_window_days,
            "regression_threshold": s.regression_threshold,
            "description": s.description,
            "active": s.name in _ACTIVE,
        }
        for s in _REGISTRY.values()
    ]


# ── Approval token ───────────────────────────────────────────────────


def _expected_token() -> Optional[str]:
    """v1: single admin token from env. v2 will replace this with a
    multi-sig 2-of-N construction reading from the key vault."""
    return os.environ.get("BRIDGE_APPROVAL_TOKEN")


# ── Activation / revocation ──────────────────────────────────────────


async def activate(
    name: str,
    *,
    value: float,
    approval_token: str,
    actor: str,
    evidence: dict,
) -> dict:
    """Activate a bridge with a calibration value.

    ``evidence`` is the operator's empirical justification — at minimum
    ``{"sample_count": N, "oos_window_days": X, "regression_pct": Y}``.
    The bridge's spec validates these against its activation criteria
    and refuses if any are below threshold.
    """
    spec = _REGISTRY.get(name)
    if spec is None:
        return {"ok": False, "reason": "unknown_bridge"}
    expected = _expected_token()
    if not expected or approval_token != expected:
        return {"ok": False, "reason": "invalid_approval_token"}

    # Validate evidence against spec
    samples = int(evidence.get("sample_count") or 0)
    if samples < spec.min_samples:
        return {"ok": False, "reason": f"insufficient_samples:{samples}<{spec.min_samples}"}
    oos = int(evidence.get("oos_window_days") or 0)
    if oos < spec.oos_window_days:
        return {"ok": False, "reason": f"insufficient_oos:{oos}<{spec.oos_window_days}"}
    regression = float(evidence.get("regression_pct") or 100.0)
    if regression > spec.regression_threshold:
        return {"ok": False, "reason": f"regression_above_threshold:{regression}>{spec.regression_threshold}"}

    # Bound the value
    lo, hi = spec.output_bounds
    if not (lo <= value <= hi):
        return {"ok": False, "reason": f"value_out_of_bounds:{value}∉[{lo},{hi}]"}

    record = {
        "name": name,
        "version": spec.version,
        "value": value,
        "actor": actor,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": evidence,
    }
    _ACTIVE[name] = {k: record[k] for k in ("value", "activated_at", "actor", "metadata")}

    if _db is not None:
        try:
            await _db["bridge_activations"].insert_one({
                **record,
                "event": "activate",
                "logged_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("[bridge] activation log write failed: %s", e)

    # ── Patent I authority minting ───────────────────────────────────
    # Each bridge activation also mints a Patent-I AuthorityScope so
    # any trade routed by this engine flows through Patent-I's risk
    # budget. The bridge's approval token IS the countersignature, so
    # future loosening requests must echo the same hashed token. We
    # store it in ``bridge_authorities`` for audit + resumability
    # across restarts. Failure is non-fatal — the bridge itself is
    # already active.
    try:
        from services.risk_budget_gateway import mint_authority_for_bridge
        scope = mint_authority_for_bridge(
            name,
            bridge_value=value,
            bridge_version=spec.version,
            activated_by=actor,
        )
        if _db is not None:
            await _db["bridge_authorities"].insert_one({
                "authority_id": scope.authority_id,
                "name": name,
                "version": spec.version,
                "tier": scope.tier.value,
                "max_multiplier": scope.max_multiplier,
                "max_notional": scope.max_notional,
                "max_daily_loss": scope.max_daily_loss,
                "expires_at": scope.expires_at.isoformat(),
                "signature_hash": scope.signature_hash,
                "minted_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:  # noqa: BLE001
        logger.warning("[bridge] patent-i authority mint failed: %s", e)

    logger.info("[bridge] activated %s=%s by %s", name, value, actor)
    return {"ok": True, "active": True, "name": name, "value": value}


async def revoke(name: str, *, actor: str, reason: str = "") -> dict:
    if name not in _ACTIVE:
        return {"ok": True, "active": False, "name": name, "noop": True}
    prev = _ACTIVE.pop(name)
    if _db is not None:
        try:
            await _db["bridge_activations"].insert_one({
                "name": name,
                "event": "revoke",
                "actor": actor,
                "reason": reason,
                "previous_value": prev.get("value"),
                "logged_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("[bridge] revoke log write failed: %s", e)
    logger.info("[bridge] revoked %s by %s (%s)", name, actor, reason or "no reason given")
    return {"ok": True, "active": False, "name": name}


# ── DTD-side consumption ─────────────────────────────────────────────


def get_calibration(name: str) -> Optional[float]:
    """Return the active calibration value for a bridge, or ``None``
    if inactive. DTD callers MUST treat ``None`` as the unmodified
    default."""
    rec = _ACTIVE.get(name)
    if not rec:
        return None
    spec = _REGISTRY.get(name)
    if spec is None:
        return None
    # Re-clamp on read as a defence-in-depth measure — even if a future
    # bug somehow lets an out-of-bounds value into _ACTIVE, the
    # consumer only ever sees a clamped value.
    val = float(rec["value"])
    lo, hi = spec.output_bounds
    return max(lo, min(hi, val))


async def audit_log(*, limit: int = 100) -> list[dict]:
    if _db is None:
        return []
    cursor = _db["bridge_activations"].find({}, {"_id": 0}).sort(
        "logged_at", -1,
    ).limit(limit)
    return await cursor.to_list(length=limit)


def active_state() -> dict:
    return {
        name: {
            "value": rec["value"],
            "activated_at": rec.get("activated_at"),
            "actor": rec.get("actor"),
        }
        for name, rec in _ACTIVE.items()
    }


# ── Default registry: pre-registered bridges ────────────────────────
# Each bridge ships **inactive** — must be activated explicitly with
# the BRIDGE_APPROVAL_TOKEN env var + evidence above thresholds.

# bridge_v1_council_calibration:
#   First production-targeted bridge. Lets PRD-derived calibration
#   nudge the Council's risk-multiplier within a tight clamp.
#   The Council's existing bounds (0.50, 1.25) remain in effect; the
#   bridge can additionally scale within [0.90, 1.10] of the Council's
#   pre-bridge multiplier — i.e. ±10% max influence.
register(BridgeSpec(
    name="bridge_v1_council_calibration",
    version="v1",
    output_target="council_risk_multiplier_scale",
    output_bounds=(0.90, 1.10),  # tight first-bridge clamp
    min_samples=200,             # 2× registry's auto-detect threshold
    oos_window_days=14,
    regression_threshold=2.0,    # max 2% regression vs current production
    description=(
        "Multiplicative nudge applied to Council's risk_multiplier on top "
        "of its existing [0.50, 1.25] bounds. PRD-derived calibration only; "
        "cannot override action direction or veto/ratify logic."
    ),
))
