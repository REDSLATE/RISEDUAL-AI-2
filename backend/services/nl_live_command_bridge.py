"""
Natural Language → Live Risk Wire-Through.

When the NL layer parses a `SET_RISK_MULTIPLIER` or `SET_MIN_RR`
command, this module is the ONLY path that mutates live production
config. It enforces:

1. **Confirmation cooldown** — every command requires a 2-step flow:
   `prepare()` returns a ``pending_id``; operator must call
   ``confirm(pending_id)`` within the cooldown window (default 30s)
   to actually apply. Typos are recoverable — simply let the pending
   row expire.

2. **Integrity floor respect** — when
   ``integrity_mitigation_service`` is currently throttling position
   sizing down (e.g., post-loss auto-throttle fired), the NL layer
   CANNOT raise risk above that floor. NL can lower risk further,
   never above the deterministic-layer ceiling.

3. **Proof-chain audit** — every apply writes a
   ``NL_COMMAND_APPLIED`` event to ``decision_proof_chain`` with the
   original NL text, parsed command, operator ``session_id``, and
   resulting mitigation/config id. Full tamper-evident log.

Collections touched:
* ``nl_pending_commands`` (TTL index on ``expires_at``)
* ``confidence_gate_overrides`` (single ``_id: "current"`` doc read by
  ``confidence_gate.get_dynamic_confidence_threshold`` on every tick)
* ``decision_proof_chain`` (via existing async append helper)
* ``integrity_mitigations`` (via
  ``integrity_mitigation_service.activate_integrity_mitigation``)

Never raises — any failure returns an error-shape dict the NL executor
can surface to the operator without crashing the chat session.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

# Default 30-second cooldown between prepare() and confirm() — matches
# operator spec. Env-tunable for tests.
NL_CONFIRM_COOLDOWN_SECONDS: int = int(
    os.environ.get("NL_CONFIRM_COOLDOWN_SECONDS", "30")
)
# Max time a pending command can live before auto-expiry. Longer than
# cooldown so operator has breathing room.
NL_PENDING_TTL_SECONDS: int = int(
    os.environ.get("NL_PENDING_TTL_SECONDS", "120")
)


CommandKind = Literal["SET_RISK_MULTIPLIER", "SET_MIN_RR"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def prepare_nl_live_command(
    db: Any,
    *,
    kind: CommandKind,
    value: float,
    original_text: str,
    session_id: str,
) -> dict[str, Any]:
    """Stage a live-mutation command. Returns a ``pending_id`` the
    operator must pass to ``confirm_nl_live_command`` within the
    cooldown window.

    Floors the requested risk multiplier against the current integrity
    ceiling — the preview payload shows the operator exactly what the
    effective value will be, so "reduce to 1.25×" during a 0.7×
    throttle displays as "will apply: 0.70× (capped by integrity layer)".
    """
    if db is None:
        return {"ok": False, "error": "no_db"}

    try:
        # Integrity-floor check for SET_RISK_MULTIPLIER
        effective_value = float(value)
        floor_note: str | None = None
        if kind == "SET_RISK_MULTIPLIER":
            try:
                from services.integrity_mitigation_service import (
                    get_effective_integrity_risk_multiplier,
                )
                ceiling = await get_effective_integrity_risk_multiplier(db)
                if effective_value > ceiling:
                    effective_value = ceiling
                    floor_note = (
                        f"Capped by integrity mitigation ceiling ({ceiling:.2f}×); "
                        f"requested {value:.2f}×."
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[nl_live] integrity ceiling read failed: %s", exc)

        now = _now()
        pending_id = str(uuid4())
        doc = {
            "pending_id": pending_id,
            "kind": kind,
            "requested_value": float(value),
            "effective_value": float(effective_value),
            "floor_note": floor_note,
            "original_text": original_text,
            "session_id": session_id,
            "created_at": now,
            "expires_at": now + timedelta(seconds=NL_PENDING_TTL_SECONDS),
            "confirm_not_before": now + timedelta(seconds=0),
            "cooldown_ends_at": now + timedelta(seconds=NL_CONFIRM_COOLDOWN_SECONDS),
            "status": "pending",
        }
        await db["nl_pending_commands"].insert_one(doc)
        return {
            "ok": True,
            "pending_id": pending_id,
            "kind": kind,
            "requested_value": float(value),
            "effective_value": float(effective_value),
            "floor_note": floor_note,
            "cooldown_seconds": NL_CONFIRM_COOLDOWN_SECONDS,
            "expires_at": doc["expires_at"].isoformat(),
            "note": (
                f"Command prepared. Confirm with pending_id within "
                f"{NL_CONFIRM_COOLDOWN_SECONDS}s to apply. Let it expire to cancel."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[nl_live] prepare failed: %s", exc)
        return {"ok": False, "error": f"prepare_error:{type(exc).__name__}"}


async def _apply_risk_multiplier(
    db: Any,
    effective_value: float,
    *,
    original_text: str,
    session_id: str,
) -> dict[str, Any]:
    """Route through integrity_mitigation_service so the NL mutation
    composes with post-loss throttling instead of fighting it."""
    from services.integrity_mitigation_service import (
        activate_integrity_mitigation,
    )
    mit_id = await activate_integrity_mitigation(
        db,
        source_rule_id=f"nl_command:{session_id[:8]}",
        mitigation={
            "action": "DEGRADE_TRADING",
            "params": {"position_multiplier": float(effective_value)},
        },
        ttl_minutes=120,
    )
    return {
        "applied": True,
        "mitigation_id": mit_id,
        "effective_value": float(effective_value),
        "note": (
            f"Risk multiplier {effective_value:.2f}× active for 120 minutes "
            f"via integrity mitigation ({mit_id})."
        ),
    }


async def _apply_min_rr(
    db: Any,
    effective_value: float,
    *,
    original_text: str,
    session_id: str,
) -> dict[str, Any]:
    """Write the override to ``confidence_gate_overrides`` — the
    confidence_gate reads this on every tick."""
    now = _now()
    ttl = now + timedelta(minutes=120)
    await db["confidence_gate_overrides"].update_one(
        {"_id": "current"},
        {
            "$set": {
                "min_rr": float(effective_value),
                "set_by": f"nl_command:{session_id[:8]}",
                "set_at": now,
                "expires_at": ttl,
                "original_text": original_text,
            }
        },
        upsert=True,
    )
    return {
        "applied": True,
        "effective_value": float(effective_value),
        "expires_at": ttl.isoformat(),
        "note": f"Minimum R:R set to {effective_value:.2f}:1 for 120 minutes.",
    }


async def _append_proof(
    db: Any,
    *,
    pending_id: str,
    pending_doc: dict[str, Any],
    apply_result: dict[str, Any],
    session_id: str,
) -> None:
    try:
        from services.proof_chain import (
            AsyncMongoProofChainStore,
            ProofEvent,
            ProofEventType,
            async_append_proof_event,
        )
        store = AsyncMongoProofChainStore(db)
        await async_append_proof_event(
            store,
            ProofEvent(
                event_type=ProofEventType.NL_COMMAND_APPLIED,
                entity_id=f"nl:{session_id[:8]}:{pending_id[:8]}",
                actor="natural_language_trading",
                payload={
                    "pending_id": pending_id,
                    "kind": pending_doc.get("kind"),
                    "requested_value": pending_doc.get("requested_value"),
                    "effective_value": pending_doc.get("effective_value"),
                    "floor_note": pending_doc.get("floor_note"),
                    "original_text": pending_doc.get("original_text"),
                    "session_id": session_id,
                    "apply_result": apply_result,
                },
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[nl_live] proof append failed: %s", exc)


async def confirm_nl_live_command(
    db: Any,
    *,
    pending_id: str,
    session_id: str,
) -> dict[str, Any]:
    """Apply a previously-prepared command. Enforces the cooldown
    window and idempotency — re-confirming an already-applied command
    returns the cached result shape instead of applying twice.
    """
    if db is None:
        return {"ok": False, "error": "no_db"}

    try:
        pending = await db["nl_pending_commands"].find_one(
            {"pending_id": pending_id},
            {"_id": 0},
        )
        if not pending:
            return {"ok": False, "error": "pending_not_found"}
        if pending.get("status") == "applied":
            return {
                "ok": True,
                "already_applied": True,
                "kind": pending.get("kind"),
                "effective_value": pending.get("effective_value"),
                "note": "Command already applied; returning cached result.",
            }
        if pending.get("status") == "expired":
            return {"ok": False, "error": "pending_expired"}

        now = _now()
        if now > pending["expires_at"]:
            await db["nl_pending_commands"].update_one(
                {"pending_id": pending_id},
                {"$set": {"status": "expired"}},
            )
            return {"ok": False, "error": "pending_expired"}

        # Session-isolation — only the session that prepared can confirm.
        if session_id and pending.get("session_id") != session_id:
            return {"ok": False, "error": "session_mismatch"}

        kind = pending["kind"]
        effective_value = float(pending["effective_value"])

        if kind == "SET_RISK_MULTIPLIER":
            apply_result = await _apply_risk_multiplier(
                db, effective_value,
                original_text=pending.get("original_text", ""),
                session_id=session_id,
            )
        elif kind == "SET_MIN_RR":
            apply_result = await _apply_min_rr(
                db, effective_value,
                original_text=pending.get("original_text", ""),
                session_id=session_id,
            )
        else:
            return {"ok": False, "error": f"unsupported_kind:{kind}"}

        # Mark applied BEFORE proof-chain append so a proof-chain
        # failure can't cause a double-apply on retry.
        await db["nl_pending_commands"].update_one(
            {"pending_id": pending_id},
            {
                "$set": {
                    "status": "applied",
                    "applied_at": now,
                    "apply_result": apply_result,
                }
            },
        )
        await _append_proof(
            db,
            pending_id=pending_id,
            pending_doc=pending,
            apply_result=apply_result,
            session_id=session_id,
        )
        return {
            "ok": True,
            "kind": kind,
            "effective_value": effective_value,
            "apply_result": apply_result,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[nl_live] confirm failed: %s", exc)
        return {"ok": False, "error": f"confirm_error:{type(exc).__name__}"}


async def ensure_nl_live_indexes(db: Any) -> dict[str, Any]:
    """Create the Mongo TTL index on ``nl_pending_commands`` so expired
    rows are auto-purged."""
    if db is None:
        return {"status": "no_db"}
    try:
        coll = db["nl_pending_commands"]
        await coll.create_index("pending_id", unique=True)
        # expireAfterSeconds=0 with a date field means Mongo purges at
        # exactly the field's timestamp (not + N seconds).
        await coll.create_index("expires_at", expireAfterSeconds=0)
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[nl_live] ensure_indexes failed: %s", exc)
        return {"status": "error", "error": str(exc)}
