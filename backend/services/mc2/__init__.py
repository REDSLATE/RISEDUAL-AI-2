"""MC2 — RISEDUAL's in-process Mission Control replacement.

Phase A scaffold (2026-06-09). Standalone-mode opt-in via
``RISEDUAL_STANDALONE_MODE=1``. When ON:

  * ``intent_bridge.emit_intent_from_consensus`` writes to
    ``mc2_intents`` instead of POSTing to Original MC.
  * ``intent_bridge.emit_opinion_from_consensus`` writes to
    ``mc2_opinions`` instead of POSTing to Original MC.
  * ``sovereign_outcome_bridge.enqueue_outcome`` also mirrors into
    ``mc2_outcomes`` so the local Scorecard rollup works.
  * The legacy sidecar check-in loop becomes a no-op (still ticks
    so we keep the heartbeat shape, but no outbound HTTP).

Doctrine:
  * No 12-gate chain in Phase A — every intent lands with
    ``gate_state="accepted_no_gates"`` and ``may_execute=False``.
    Phase B ports the gates.
  * No frontend — Phase A is backend-only. Operator triage uses
    ``GET /api/admin/mc2/state``.
  * The Original-MC wire is SEVERED but the code path is NOT
    deleted — if the operator unsets the env var, behaviour
    snaps back to the legacy remote-MC flow for safe rollback.

Public API:
    from services.mc2 import (
        is_standalone,
        post_intent_local,
        post_opinion_local,
        record_outcome_local,
        get_state,
        get_scorecard,
        set_db as mc2_set_db,
    )
"""
from __future__ import annotations

from services.mc2.standalone import is_standalone
from services.mc2.intents import post_intent_local
from services.mc2.opinions import post_opinion_local
from services.mc2.outcomes import record_outcome_local
from services.mc2.scorecard import get_scorecard
from services.mc2.state import get_state, set_db

__all__ = [
    "is_standalone",
    "post_intent_local",
    "post_opinion_local",
    "record_outcome_local",
    "get_state",
    "get_scorecard",
    "set_db",
]
