"""Patent M (Alpha) — Learning Core diagnostic endpoint.

Read-only operator surface. Per the Alpha IP rollout protocol
(operator-defined Feb 2026), the learning core is shipped FIRST
as a diagnostic only. No wire-up into Strategist / Auditor /
Commander / Council / execution until each subsequent rollout
step is explicitly approved.

Hard rollout protocol
---------------------
1. Diagnostic endpoint                     ← THIS MODULE
2. Read-only corridor annotation           ← gated on review
3. Shadow confidence delta logging         ← gated on review
4. Review 50–100 cycles                    ← human checkpoint
5. Gated confidence influence              ← gated on review

Hard Alpha rules (verified at code review, NOT at runtime — the
runtime cannot enforce "must not be wired" because that's a code-
structure constraint, not a value-comparison):

* Cannot change action / direction / size.
* Cannot promote HOLD / UNKNOWN.
* Cannot bypass Commander or Council.
* Cannot write execution state.
* Cannot place orders.

This endpoint:

* GET — returns ``RisedualLearningCore.to_dict()`` (pure values)
        plus a small surface of process-level metadata.

That's it. No POST. No mutation. No DB write. No broker call.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-learning-core"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    """Owner-only — diagnostic surfaces feature dimensions and
    confusion-matrix signal that we treat as IP-sensitive."""
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


@router.get("/learning-core/diagnostic")
async def learning_core_diagnostic(request: Request) -> dict[str, Any]:
    """Return a read-only snapshot of the learning core's state.

    Response shape::

        {
          "rollout_step": 1,                  # 1..5 (operator gate)
          "rollout_step_label": "diagnostic",
          "wired_into_decision_flow": False,  # Alpha hard rule
          "env_flags": {
              "LEARNING_CORE_SHADOW_ENABLED": False,
              "LEARNING_CORE_CONSUME_ENABLED": False,
              "LEARNING_CORE_PERSISTENCE_ENABLED": False,
              "LEARNING_CORE_REHYDRATE_ON_STARTUP": False,
          },
          "core": {
              "cacl": {...},                  # to_dict() output
              "regime_memory": {...},
              "guardrails": {...},
          },
        }

    The ``rollout_step`` field is the operator's at-a-glance
    answer to *"how integrated is Patent M right now?"*. Bumping
    that number is gated on this endpoint's review evidence.
    """
    await _require_owner(request)

    # Lazy import — keeps the learning core's NumPy dependency out
    # of the cold-start path. The singleton is allocated on first
    # call to ``get_core()`` and remains process-local thereafter.
    from services.learning_core_service import get_core

    try:
        core = get_core()
        core_state = core.to_dict()
    except Exception as exc:
        logger.exception("learning_core_diagnostic: to_dict failed")
        raise HTTPException(
            status_code=500,
            detail=f"learning core to_dict failed: {exc}",
        ) from exc

    env_flags = {
        "LEARNING_CORE_SHADOW_ENABLED": _bool_env(
            "LEARNING_CORE_SHADOW_ENABLED",
        ),
        "LEARNING_CORE_CONSUME_ENABLED": _bool_env(
            "LEARNING_CORE_CONSUME_ENABLED",
        ),
        "LEARNING_CORE_PERSISTENCE_ENABLED": _bool_env(
            "LEARNING_CORE_PERSISTENCE_ENABLED",
        ),
        "LEARNING_CORE_REHYDRATE_ON_STARTUP": _bool_env(
            "LEARNING_CORE_REHYDRATE_ON_STARTUP",
        ),
    }

    return {
        # Rollout step is HARDCODED here — the only way to bump it
        # is through a code review that also wires the next layer.
        # Treat this as a self-attesting badge, not a runtime
        # toggle.
        "rollout_step": 1,
        "rollout_step_label": "diagnostic",
        "wired_into_decision_flow": False,
        "env_flags": env_flags,
        "core": core_state,
        # Operator-facing metrics that depend on rollout steps 2/3
        # being approved and wired. Surfaced here as explicit
        # "awaiting" envelopes so the Shelly tile can render the
        # rollout state honestly without pretending to have data.
        "awaiting_rollout": {
            "recent_confidence_deltas": {
                "available_after_step": 3,
                "label": "shadow confidence delta logging",
                "samples": [],
            },
            "hold_suppression_counts": {
                "available_after_step": 5,
                "label": "gated confidence influence",
                "count": 0,
            },
            "retrieval_confidence_history": {
                "available_after_step": 2,
                "label": "read-only corridor annotation",
                "samples": [],
            },
        },
    }


def _bool_env(name: str) -> bool:
    return os.getenv(name, "false").lower() == "true"
