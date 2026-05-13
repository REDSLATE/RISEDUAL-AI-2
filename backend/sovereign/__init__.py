"""Sovereign sidecar kit — Alpha brain runtime.

Doctrine:
    1. Separate brains on separate hosts (or strictly isolated processes),
       talking to Mission Control ONLY via HTTP.
    2. The runtime never writes directly to MC's database.
    3. ``LIVE_TRADING_ENABLED`` is hard-locked to ``False`` at module level;
       the sidecar refuses to boot if anyone flips it.
    4. PRD mode forbids ``training_signal=True`` payloads.

Kit modules:
    - wild_adaptive_core_v2: doctrine constants + the live-trading lock
    - local_state:           per-brain weights / mode / outcomes persistence
    - mc_client:             HTTP client for heartbeat + contribution
    - sidecar:               60s tick loop entrypoint
    - smoke_test:            offline doctrine validation (no MC needed)
    - bootstrap_alpha:       one-shot weight seeder
"""

__all__ = [
    "wild_adaptive_core_v2",
    "local_state",
    "mc_client",
    "sidecar",
]
