"""
Sovereign AI — Mode Guard.

Controls which operations are permitted in DTD (research / challenger /
training) vs PRD (production / shadow + bounded contribution) mode.

The active mode is read from the ``RISEDUAL_CORE_MODE`` env var. Default is
``PRD`` — the safer default — so a missing flag can never accidentally
enable training on a live deployment.

Usage:
    from services.sovereign_mode_guard import require_dtd, require_prd

    def nightly_retrain(...):
        require_dtd()      # raises RuntimeError if not DTD
        ...

    def apply_promoted_contribution(...):
        require_prd()      # raises RuntimeError if not PRD
        ...
"""
from __future__ import annotations

import os
from typing import Literal

CoreMode = Literal["DTD", "PRD"]


def get_core_mode() -> CoreMode:
    """Return the current core mode. Default is ``PRD`` (production)."""
    raw = (os.environ.get("RISEDUAL_CORE_MODE") or "PRD").strip().upper()
    if raw not in {"DTD", "PRD"}:
        raw = "PRD"
    return raw  # type: ignore[return-value]


def is_dtd() -> bool:
    return get_core_mode() == "DTD"


def is_prd() -> bool:
    return get_core_mode() == "PRD"


def require_dtd() -> None:
    """Hard-fail if not running in DTD mode."""
    if not is_dtd():
        raise RuntimeError(
            f"DTD-only operation blocked (current mode: {get_core_mode()})"
        )


def require_prd() -> None:
    """Hard-fail if not running in PRD mode."""
    if not is_prd():
        raise RuntimeError(
            f"PRD-only operation blocked (current mode: {get_core_mode()})"
        )
