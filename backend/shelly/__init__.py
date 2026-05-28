"""5-Shelly Federation — module init.

A per-brain memory + reasoning sidecar federation. Each brain in
the RISEDUAL multi-agent stack (Alpha / Camaro / Chevelle / RedEye)
gets its own ``LocalShelly``; an ``MCShelly`` head ingests rollups
and reasons across the federation.

Doctrine (the only invariant that ships)
----------------------------------------
Every document this module writes carries::

    "authority": "memory_reasoning_only"

Shelly may say:
  * "support"
  * "warn"
  * "neutral"
  * "seen before"
  * "loss pattern"
  * "conflict between brains"

Shelly may NOT say:
  * "execute"
  * "block"
  * "override"
  * "promote"

Execution authority remains with the brains; safety remains with
RoadGuard; verification remains with Mission Control. Shelly is
the memory + reasoning sidecar — read-only influence.
"""
from shelly.contracts import (  # noqa: F401
    ShellyMemoryEvent,
    ShellyReasoningReceipt,
    stable_hash,
    utc_now,
)
from shelly.config import BRAIN_NAMES, MEMORY_REASONING_ONLY  # noqa: F401
from shelly.local_shelly import LocalShelly  # noqa: F401
from shelly.mc_shelly import MCShelly  # noqa: F401
from shelly.pipeline import ShellyPipeline  # noqa: F401

__all__ = [
    "BRAIN_NAMES",
    "MEMORY_REASONING_ONLY",
    "ShellyMemoryEvent",
    "ShellyReasoningReceipt",
    "stable_hash",
    "utc_now",
    "LocalShelly",
    "MCShelly",
    "ShellyPipeline",
]
