"""
AI Promotion History — tamper-evident audit trail for every phase
transition across the Sovereign AI, Adversarial Cores, Equity
Commander Shadow, and any future promotable core.

Why this exists
───────────────
Promotions are today driven by a combination of:

* **Automated readiness gates** (``sovereign_promotion_gate``,
  ``adversarial_promotion_gate``, ``equity_shadow_promotion``) that
  compute ``ready_to_promote`` bool + blocker strings.
* **Operator env-flag flips** (e.g., ``CRYPTO_ADVERSARIAL_PHASE=
  risk_only``) followed by a backend restart — the audit-stable
  discipline flagged in the adversarial_promotion_gate docstring.

Before this module, there was NO record of *when* a phase changed,
*who* changed it, *why*, or *what the gate's metrics looked like at
the moment of the change*. This made it hard to retrospectively
review whether a promotion was premature or overdue.

Now every phase observed at boot is diffed against the latest
recorded row; when a change is detected, a new
``ai_promotion_history`` row lands capturing the transition + a
full snapshot of the gate's metrics. Operators can also record
transitions manually (via the admin endpoint) with free-text
notes for post-hoc annotation.

Shape
─────
Each row::

    {
        "core": "adversarial" | "sovereign_equity" | "sovereign_crypto"
              | "equity_shadow_commander",
        "from_phase": str,
        "to_phase": str,
        "at": datetime (UTC),
        "actor": "auto_boot_detector" | "operator" | <email>,
        "reason": str,                          # free-text or code
        "metrics": dict,                        # gate snapshot
    }

Read-only otherwise — the writer is the module itself (at boot, via
``detect_phase_changes_at_startup``) and the admin endpoint. No
downstream service consumes this collection for decision-making —
it's purely observational.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION: str = "ai_promotion_history"

# Registry of every promotable core.
# Each entry declares:
#   * current_phase_reader — callable(db) → str (sync or async)
#   * metrics_reader       — async callable(db) → dict
#   * env_var              — the env flag that drives the phase
CORE_REGISTRY: dict[str, dict[str, Any]] = {
    "adversarial": {
        "env_var": "CRYPTO_ADVERSARIAL_PHASE",
        "default_phase": "shadow",
    },
    "sovereign_equity": {
        "env_var": "SOVEREIGN_EQUITY_PHASE",
        "default_phase": "phase_1_shadow_only",
    },
    "sovereign_crypto": {
        "env_var": "SOVEREIGN_CRYPTO_PHASE",
        "default_phase": "phase_1_shadow_only",
    },
    "equity_shadow_commander": {
        "env_var": "EQUITY_SHADOW_COMMANDER_PHASE",
        "default_phase": "phase_1_logging_only",
    },
}


def _read_current_phase(core: str) -> str:
    cfg = CORE_REGISTRY.get(core)
    if cfg is None:
        return "unknown"
    return os.environ.get(cfg["env_var"], cfg["default_phase"])


async def _snapshot_metrics(db: Any, core: str) -> dict[str, Any]:
    """Best-effort metrics snapshot per core. Never raises."""
    if db is None:
        return {}
    try:
        if core == "adversarial":
            from services.adversarial_promotion_gate import (
                compute_promotion_status,
            )
            return await compute_promotion_status(db)
        if core in ("sovereign_equity", "sovereign_crypto"):
            from services.sovereign_promotion_gate import (
                compute_sovereign_promotion_status,
            )
            lane = (
                "equity" if core == "sovereign_equity" else "crypto"
            )
            return await compute_sovereign_promotion_status(db, lane)
        if core == "equity_shadow_commander":
            from services.equity_shadow_promotion import (
                compute_equity_shadow_promotion_status,
            )
            return await compute_equity_shadow_promotion_status(db)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[promotion-history] metrics snapshot failed for %s: %s", core, exc)
    return {}


async def record_promotion(
    db: Any, *, core: str, from_phase: str, to_phase: str,
    actor: str = "operator", reason: str = "",
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert one row describing a phase transition.

    Safe to call even when ``from_phase == to_phase`` — in that case
    the row acts as a manual annotation (operator note about the
    current state). Never raises.
    """
    row = {
        "core": core,
        "from_phase": from_phase,
        "to_phase": to_phase,
        "at": datetime.now(timezone.utc),
        "actor": actor,
        "reason": reason,
        "metrics": metrics or {},
    }
    if db is None:
        return row
    try:
        await db[COLLECTION].insert_one(dict(row))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[promotion-history] write failed: %s", exc)
    # Pop any _id the driver stamped on the dict we passed.
    row.pop("_id", None)
    return row


async def get_promotion_history(
    db: Any, *, core: str | None = None, limit: int = 50,
) -> list[dict[str, Any]]:
    """Newest-first history rows. Optional ``core`` filter."""
    if db is None:
        return []
    query: dict[str, Any] = {}
    if core:
        query["core"] = core
    try:
        cursor = db[COLLECTION].find(query, {"_id": 0}).sort("at", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
        for r in rows:
            at = r.get("at")
            if hasattr(at, "isoformat"):
                r["at"] = at.isoformat()
        return rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("[promotion-history] read failed: %s", exc)
        return []


async def _get_latest_phase(db: Any, core: str) -> str | None:
    """Most recent ``to_phase`` for a core, or ``None`` if no rows."""
    if db is None:
        return None
    try:
        row = await db[COLLECTION].find_one(
            {"core": core}, {"_id": 0, "to_phase": 1},
            sort=[("at", -1)],
        )
        return row.get("to_phase") if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[promotion-history] latest-phase read failed: %s", exc)
        return None


async def detect_phase_changes_at_startup(db: Any) -> list[dict[str, Any]]:
    """Walk every registered core, compare current env-driven phase
    against the last-recorded row, insert transition rows for any
    delta. Idempotent — unchanged cores produce no writes.

    Returns the list of rows that were inserted (empty on cold-start
    for each core AFTER the first boot). Never raises.
    """
    if db is None:
        return []
    inserted: list[dict[str, Any]] = []
    for core in CORE_REGISTRY:
        current = _read_current_phase(core)
        last = await _get_latest_phase(db, core)
        if last is None:
            # Cold-start for this core — seed with the current phase
            # so any future transition has a "from" anchor.
            metrics = await _snapshot_metrics(db, core)
            row = await record_promotion(
                db, core=core,
                from_phase="(initial)",
                to_phase=current,
                actor="auto_boot_detector",
                reason="initial_phase_seeded_at_boot",
                metrics=metrics,
            )
            inserted.append(row)
            continue
        if last != current:
            metrics = await _snapshot_metrics(db, core)
            row = await record_promotion(
                db, core=core,
                from_phase=last, to_phase=current,
                actor="auto_boot_detector",
                reason="env_phase_changed_since_last_boot",
                metrics=metrics,
            )
            inserted.append(row)
    return inserted
