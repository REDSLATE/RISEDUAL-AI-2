"""Sovereign doctrine constants — Alpha brain core.

This module exists to host ONE constant that is checked by the sidecar
before every boot. The sidecar will refuse to start if anyone (a future
agent, a misguided operator, a test bug) flips this to ``True``.

Three locks for one door:
    Lock 1 — this constant being literally False at module level
    Lock 2 — the sidecar's pre-boot check raising RuntimeError if violated
    Lock 3 — MC's API schema rejecting ``live_trading_enabled: true`` with 422

If you find yourself wanting to set LIVE_TRADING_ENABLED=True for "testing":
stop. There is nothing to test live in Phase 1. The synthetic stub gives
you everything you need.
"""
from __future__ import annotations

# ── LOCK #1 ──────────────────────────────────────────────────────────
# Do NOT change. Required to be literally False (not 0, not "false", not None).
LIVE_TRADING_ENABLED: bool = False

# Cosmetic identity — used in logs + MC's runtime page header.
BRAIN_NAME: str = "alpha"
BRAIN_PERSONALITY: str = "trend_follower"
SUPPORTED_MODES: tuple[str, ...] = ("DTD", "PRD")

# Action → stance mapping (kept here for stance v2; v1 skips stance entirely).
ACTION_TO_STANCE: dict[str, str] = {
    "BUY": "long",
    "SELL": "short",
    "HOLD": "abstain",
}

# Allowed actions for ``recent_outcomes[*].action`` (validated client-side).
ALLOWED_ACTIONS: frozenset[str] = frozenset({"BUY", "SELL", "HOLD"})
# Allowed outcome ints for ``recent_outcomes[*].outcome``.
ALLOWED_OUTCOMES: frozenset[int] = frozenset({-1, 0, 1})


def assert_doctrine() -> None:
    """LOCK #2. Sidecar calls this on boot and refuses to start on failure.

    Anything that looks remotely like "live trading was enabled" trips it.
    Re-check by identity, not just truthiness — defends against tricks like
    ``LIVE_TRADING_ENABLED = object()`` accidentally being truthy.
    """
    if LIVE_TRADING_ENABLED is not False:
        raise RuntimeError(
            "DOCTRINE VIOLATION: wild_adaptive_core_v2.LIVE_TRADING_ENABLED "
            "is not False. Sidecar refusing to start. Reset to False and "
            "redeploy."
        )
