"""Configuration constants for the 5-Shelly federation."""
from __future__ import annotations

# The four execution-authority brains in the RISEDUAL stack.
# A LocalShelly is instantiated per name.
BRAIN_NAMES: tuple[str, ...] = ("Alpha", "Camaro", "Chevelle", "RedEye")

# The MC node also participates in the federation with its OWN local
# Shelly — MC is the verifier/notary and produces receipts of its
# own (verification verdicts, council-policy decisions, sovereign
# promotion gate calls) that deserve the same memory + reasoning
# treatment as a brain. Keep MC strictly separate from
# ``BRAIN_NAMES`` because MC is NOT a brain — it has no execution
# authority — but it IS a federation node.
MC_NODE_NAME: str = "MC"

# All 5 federation nodes — every node owns a LocalShelly. The
# MCShelly aggregator/head reasons ACROSS these 5 instances; it is
# itself a function, not an additional Shelly count.
NODE_NAMES: tuple[str, ...] = BRAIN_NAMES + (MC_NODE_NAME,)

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
    "MC_NODE_NAME",
    "NODE_NAMES",
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
