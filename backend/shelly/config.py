"""Configuration constants for the 5-Shelly federation."""
from __future__ import annotations

# The four execution-authority brains in the RISEDUAL stack.
# A LocalShelly is instantiated per name. Order matters only for
# deterministic iteration in rollup jobs.
BRAIN_NAMES: tuple[str, ...] = ("Alpha", "Camaro", "Chevelle", "RedEye")

# Authority discipline tag. EVERY document Shelly writes must carry
# this exact string so the operator (and audit tooling) can verify
# at a glance that no Shelly receipt has been quietly upgraded into
# something with execution power.
MEMORY_REASONING_ONLY = "memory_reasoning_only"

# Reasoning thresholds. Tuned conservative so a tiny sample size
# can't bend a brain's confidence. Both layers use the same defaults
# but MC's higher sample floor reflects the larger pooled population.
LOCAL_MIN_SAMPLES = 5
LOCAL_LOSS_RATE_WARN = 0.60
LOCAL_CONFIDENCE_DELTA_WARN = -0.15

MC_MIN_SAMPLES = 10
MC_LOSS_RATE_WARN = 0.60
MC_LOSS_RATE_SUPPORT = 0.35
MC_CONFIDENCE_DELTA_WARN = -0.20
MC_CONFIDENCE_DELTA_SUPPORT = 0.10

# Recency window for "similar past cases" lookups. Reasoning ignores
# memories older than this so an outdated regime doesn't drag the
# verdict.
LOCAL_SIMILAR_LIMIT = 25
MC_SIMILAR_LIMIT = 100

__all__ = [
    "BRAIN_NAMES",
    "MEMORY_REASONING_ONLY",
    "LOCAL_MIN_SAMPLES",
    "LOCAL_LOSS_RATE_WARN",
    "LOCAL_CONFIDENCE_DELTA_WARN",
    "MC_MIN_SAMPLES",
    "MC_LOSS_RATE_WARN",
    "MC_LOSS_RATE_SUPPORT",
    "MC_CONFIDENCE_DELTA_WARN",
    "MC_CONFIDENCE_DELTA_SUPPORT",
    "LOCAL_SIMILAR_LIMIT",
    "MC_SIMILAR_LIMIT",
]
