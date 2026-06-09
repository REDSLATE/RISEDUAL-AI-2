"""MC2 standalone-mode toggle.

The single source of truth for "is the Original MC wire severed?".
All MC2 wire-in points (intent_bridge, sovereign_outcome_bridge,
mc_checkin loop) consult ``is_standalone()`` rather than reading
the env var directly — keeps the doctrine auditable in one place
and lets tests flip behaviour with ``monkeypatch.setenv``.
"""
from __future__ import annotations

import os


_TRUTHY = {"1", "true", "True", "yes", "on", "TRUE", "YES", "ON"}


def is_standalone() -> bool:
    """True when ``RISEDUAL_STANDALONE_MODE`` is set to a truthy
    value. Default OFF — flipping the env var on a deployed pod
    cuts the Original-MC wire on the next request without a code
    change.
    """
    raw = (os.environ.get("RISEDUAL_STANDALONE_MODE") or "").strip()
    return raw in _TRUTHY


__all__ = ["is_standalone"]
