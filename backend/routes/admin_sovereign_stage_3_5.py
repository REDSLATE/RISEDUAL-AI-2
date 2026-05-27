"""Stage 3.5 Promotion Matrix — read-only admin endpoint (2026-02-23).

Returns the live (asset_type, regime) promotion matrix so MC's
dashboard + operator inspection both have a single source. No
write surface — promotion state is computed from
``sovereign_decisions`` and the global gate; the only operator
controls today are the env knobs
(``STAGE_3_5_MIN_RESOLVED_PER_CONTEXT``,
``STAGE_3_5_DEMOTE_BELOW_WIN_RATE``).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/sovereign", tags=["admin-sovereign-stage-3-5"])

db: Any = None


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


@router.get("/promotion-matrix")
async def get_promotion_matrix(request: Request) -> dict[str, Any]:
    """Per-context promotion matrix for both asset types.

    Response shape::

        {
          "equity": {
            "global_promoted": bool,
            "global_blocker":  str|None,
            "cells": [{asset_type, regime, resolved, wins, win_rate,
                       promoted_in_context, demoted_in_context, blocker}, ...]
          },
          "crypto": { ... same shape ... }
        }
    """
    await _require_owner(request)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    from services.sovereign_promotion_gate import (
        compute_sovereign_promotion_status,
    )
    from services.sovereign_promotion_stage_3_5 import (
        compute_promotion_matrix,
    )

    out: dict[str, Any] = {}
    for asset_type in ("equity", "crypto"):
        try:
            global_state = await compute_sovereign_promotion_status(
                db, asset_type,
            )
            cells = await compute_promotion_matrix(
                db, asset_type=asset_type,
                global_promoted=bool(global_state.get("promoted")),
            )
            out[asset_type] = {
                "global_promoted": bool(global_state.get("promoted")),
                "global_blocker": global_state.get("blocker"),
                "global_demoted": bool(global_state.get("demoted")),
                "global_win_rate": global_state.get("win_rate"),
                "cells": cells,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[stage_3_5_route] %s matrix failed: %s", asset_type, exc,
            )
            out[asset_type] = {"error": str(exc)}
    return out


__all__ = ["router", "set_db"]
