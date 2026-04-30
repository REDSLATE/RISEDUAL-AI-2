"""Shadow-mode logger for the Decision Pipeline Guard.

When ``GUARD_SHADOW_MODE=1`` the guard runs end-to-end (Patents
K → M → I → J), but its verdict is RECORDED rather than ENFORCED.
The bot/route then proceeds with the legacy execution path. This
collection lets operators compare "what the guard would have done"
vs "what actually happened" before flipping the guard to enforcement.

Single writer; never raises (a logging failure must not block
legitimate trading).
"""
from __future__ import annotations

__domain__ = "DTD"

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

COLLECTION = "guard_shadow_log"


def is_shadow_mode_enabled() -> bool:
    """Return True when ``GUARD_SHADOW_MODE`` is on.

    Default is OFF — once shadow data has been collected and reviewed
    the operator flips this to enforcement by setting the flag to 0.
    """
    raw = os.environ.get("GUARD_SHADOW_MODE", "0") or ""
    return raw.lower() in ("1", "true", "yes", "on")


async def ensure_indexes(db: Any) -> None:
    """Indexes the shadow log needs. Idempotent."""
    if db is None:
        return
    try:
        coll = db[COLLECTION]
        await coll.create_index([("created_at", -1)])
        await coll.create_index([("source", 1), ("created_at", -1)])
        await coll.create_index([("would_allow", 1), ("created_at", -1)])
        await coll.create_index([("entity_id", 1)])
    except Exception:  # noqa: BLE001
        pass


async def log_guard_shadow_decision(
    db: Any,
    *,
    source: str,
    entity_id: str,
    guard_result: dict,
    executed_action: Optional[str] = None,
    executed_notional: Optional[float] = None,
    context: Optional[dict] = None,
) -> None:
    """Record one shadow-mode guard verdict.

    Parameters
    ----------
    source:
        Where the call came from. ``"crypto_bot"`` /
        ``"manual_order:smart_orders"`` / ``"manual_order:options"`` etc.
    entity_id:
        The same ``entity_id`` passed to the guard pipeline. Lets
        operators correlate a shadow row with its proof-chain blocks.
    guard_result:
        The full dict returned by ``run_guarded_decision_pipeline_async``.
    executed_action / executed_notional:
        What the legacy path actually did (or will do). Used for the
        "old vs guarded" diff that drives the rollout decision.
    context:
        Free-form route/symbol metadata for filtering in the admin UI.
    """
    if db is None:
        return
    try:
        doc = {
            "source": source,
            "entity_id": entity_id,
            "would_allow": bool(guard_result.get("allow")),
            "would_action": guard_result.get("action"),
            "would_notional": float(guard_result.get("notional") or 0.0),
            "would_risk_multiplier": float(guard_result.get("risk_multiplier") or 0.0),
            "reasons": list(guard_result.get("reasons") or [])[:8],
            "proof_hashes": list(guard_result.get("proof_hashes") or []),
            "adversarial": _shrink(guard_result.get("adversarial")),
            "failure_mode": _shrink(guard_result.get("failure_mode")),
            "risk_budget": _shrink(guard_result.get("risk_budget")),
            "executed_action": executed_action,
            "executed_notional": (
                float(executed_notional) if executed_notional is not None else None
            ),
            "context": dict(context or {}),
            "created_at": datetime.now(timezone.utc),
        }
        await db[COLLECTION].insert_one(doc)
    except Exception as e:  # noqa: BLE001 — never block on log failure
        logger.warning("[guard_shadow_log] insert failed: %s", e)


def _shrink(d: Optional[dict]) -> Optional[dict]:
    """Trim verbose nested dicts to just the operator-relevant fields.

    The full proof chain row already has the verbose payload; the
    shadow log is a summary index, not a duplicate of the chain.
    """
    if not d:
        return None
    keep = {
        "action", "confidence", "dissent_score", "allowed_to_trade", "reason",
        "mode", "risk_multiplier_cap", "block_trade",
        "allowed", "final_notional", "final_multiplier", "authority_tier",
        "tightened", "loosened",
    }
    return {k: v for k, v in d.items() if k in keep}
