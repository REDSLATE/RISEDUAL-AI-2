"""Public ``GET /api/status`` route — MC v1 brain identity surface.

Returns the v1 identity block consumed by MC's BrainHealthTile /
BrainProxiedStatusTile. The route is intentionally PUBLIC (no auth) —
the payload contains only env-presence booleans + app metadata, no
secrets or token values. MC reads it through its brain-proxy on each
dashboard refresh.

See:
- ``sidecar/mc_identity_v1.py`` — verbatim drop-in from MC
- ``sidecar/BRAIN_IDENTITY_HANDOFF.md`` — v1 contract
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["public-status"])


SIDECAR_VERSION = "1.0.0"
APP_NAME = "alpha"


@router.get("/api/status")
async def public_status() -> dict[str, Any]:
    """v1 brain identity block — read by MC's dashboard."""
    from sidecar.mc_identity_v1 import build_identity_block
    return {
        "identity": build_identity_block(
            app_name=APP_NAME,
            sidecar_version=SIDECAR_VERSION,
        ),
    }


__all__ = ["router"]
