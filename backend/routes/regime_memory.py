"""Admin read-only routes for the Regime Memory Retrieval layer.

Kept in its own module rather than ``routes/admin.py`` because that
file is already on the size-allowlist (4500+ lines, refactor debt).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.regime_memory_retrieval import (
    RegimeFingerprint,
    regime_memory_engine,
)

router = APIRouter(prefix="/api/admin/regime-memory", tags=["admin-regime-memory"])

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    """Wire the Mongo handle (currently unused — engine is in-memory).

    Kept for parity with other admin routes in case we later persist
    cluster centroids or audit retrieval calls.
    """
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


class _FingerprintIn(BaseModel):
    vix_level: str = Field(..., examples=["normal"])
    yield_curve: str = Field(..., examples=["flat"])
    dxy_trend: str = Field(..., examples=["strong"])
    credit_spreads: str = Field(..., examples=["tight"])
    liquidity: str = Field(..., examples=["normal"])
    macro_phase: str = Field(..., examples=["late_cycle"])

    def to_fingerprint(self) -> RegimeFingerprint:
        return RegimeFingerprint(
            vix_level=self.vix_level,
            yield_curve=self.yield_curve,
            dxy_trend=self.dxy_trend,
            credit_spreads=self.credit_spreads,
            liquidity=self.liquidity,
            macro_phase=self.macro_phase,
        )


class _RiskContextIn(BaseModel):
    ticker: str
    direction: str
    current_regime: _FingerprintIn
    current_pretell: Optional[_FingerprintIn] = None


@router.get("/report")
async def get_regime_memory_report(request: Request):
    """Owner-only diagnostic snapshot of the in-memory engine state."""
    await _require_owner(request)
    return regime_memory_engine.report()


@router.post("/risk-context/preview")
async def preview_risk_context(payload: _RiskContextIn, request: Request):
    """Read-only preview of the bounded risk context the engine would
    emit for a given regime + ticker + direction. Does not mutate any
    cluster, does not write to any collection."""
    await _require_owner(request)
    pretell = payload.current_pretell.to_fingerprint() if payload.current_pretell else None
    return regime_memory_engine.build_risk_context(
        current_regime=payload.current_regime.to_fingerprint(),
        current_pretell=pretell,
        ticker=payload.ticker,
        direction=payload.direction,
    )
