"""RISEDUAL Learning Core — process-wide service surface.

Patent M Phase 3 plumbing.

Centralises three concerns that Phase 1/2 left scattered:

1. **Process-wide singleton.** Phase 2's shadow hook spun up its
   own private core; Phase 3's consumer needs the same instance
   so resolved memories accumulated via ``add_and_persist_memory``
   are visible to ``evaluate_context`` calls from the shadow hook.
   The shadow hook now imports the singleton from here.

2. **Persist + ingest atom.** ``add_and_persist_memory`` is the
   public entry-point operators / schedulers should call when a
   trade resolves. It first ingests the memory into the in-memory
   regime engine (the source of truth) and then opportunistically
   persists it to Mongo. Persistence failure NEVER affects the
   in-memory ingest result — that contract is load-bearing for
   the IP guarantee that "in-memory state is canonical".

3. **Rehydrate.** ``rehydrate_core_from_mongo`` pulls the most
   recent N persisted memories, oldest-first, and replays them
   through the singleton. Persistence is bypassed during replay
   (the rows are already in Mongo). Returns a structured envelope
   so the startup hook can log how many memories were restored.

All three concerns are env-flag gated so a misconfigured pod can
never accidentally activate the persistence layer.

Env flags
---------
* ``LEARNING_CORE_PERSISTENCE_ENABLED`` — gates writes (read by
  ``learning_core_persistence``).
* ``LEARNING_CORE_REHYDRATE_ON_STARTUP`` — gates the boot-time
  replay (read here). Default off so a fresh pod boots cold.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from services.learning_core_persistence import (
    persist_resolved_memory,
    rehydrate_resolved_memories,
)
from services.regime_clustering_layer import RegimeLabeledMemory
from services.risedual_learning_core import RisedualLearningCore


logger = logging.getLogger(__name__)


# Singleton config — must match the shadow hook's previous local
# defaults so the two paths share the same model dimensions.
DEFAULT_FEATURE_DIM = 8
DEFAULT_N_CLASSES = 3


_singleton: RisedualLearningCore | None = None


def get_core() -> RisedualLearningCore:
    """Lazy process-wide singleton. Cheap to construct (NumPy
    weights only) but only built on first use so cold-start time
    is unaffected when the learning core is fully disabled."""
    global _singleton
    if _singleton is None:
        _singleton = RisedualLearningCore(
            input_dim=DEFAULT_FEATURE_DIM,
            n_classes=DEFAULT_N_CLASSES,
        )
    return _singleton


def reset_singleton_for_tests() -> None:
    """Test escape hatch — clears the lazy singleton between tests
    so cluster state from one test doesn't leak into the next."""
    global _singleton
    _singleton = None


async def add_and_persist_memory(
    db: Any,
    memory: RegimeLabeledMemory,
) -> dict[str, Any]:
    """Public entry-point: ingest a resolved memory and best-effort
    persist it.

    The in-memory ingest result is the canonical answer — Mongo
    persistence is opportunistic. If Mongo is down the cluster
    state still updates.
    """
    core = get_core()
    in_memory = core.add_resolved_memory(memory)
    persist = await persist_resolved_memory(db, memory)
    return {**in_memory, "persistence": persist}


def _rehydrate_enabled() -> bool:
    return os.getenv(
        "LEARNING_CORE_REHYDRATE_ON_STARTUP", "false",
    ).lower() == "true"


async def rehydrate_core_from_mongo(
    db: Any,
    limit: int = 5000,
) -> dict[str, Any]:
    """Boot-time helper. Loads up to ``limit`` resolved memories
    from Mongo (newest-first, then reversed to chronological), and
    replays them through the singleton. Persistence is bypassed
    during replay — the rows are already in Mongo and we don't
    want to re-write them.

    Gated on ``LEARNING_CORE_REHYDRATE_ON_STARTUP``. Returns an
    envelope with ``loaded`` / ``replayed`` counts so the caller
    can log a single readable line.

    Failure modes:
      * env flag off → ``{"loaded": 0, "replayed": 0, "skipped": "env_flag_off"}``
      * Mongo error  → ``{"loaded": 0, "replayed": 0, "skipped": "..."}`` (never raises)
    """
    if not _rehydrate_enabled():
        return {"loaded": 0, "replayed": 0, "skipped": "env_flag_off"}

    try:
        memories = await rehydrate_resolved_memories(db, limit=limit)
    except Exception as exc:
        logger.warning("learning_core: rehydrate load failed: %s", exc)
        return {"loaded": 0, "replayed": 0, "skipped": str(exc)}

    if not memories:
        return {"loaded": 0, "replayed": 0}

    core = get_core()
    replayed = 0
    for mem in memories:
        try:
            core.add_resolved_memory(mem)
            replayed += 1
        except Exception as exc:
            # One bad memory must not stop the whole replay — that
            # would punish the operator for a single corrupt row.
            logger.warning(
                "learning_core: replay skipped memory_id=%s err=%s",
                getattr(mem, "memory_id", "?"),
                exc,
            )

    return {"loaded": len(memories), "replayed": replayed}
