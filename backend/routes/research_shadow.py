"""Admin endpoints for the Research Shadow framework.

All endpoints sit under ``/api/admin/shadow/`` and require admin/owner
role. Read-only — there are intentionally NO mutating endpoints here.
Bot-level config (``shadow_engine``, ``shadow_paused``) is set via
the existing ``trading_bots`` admin tooling, which keeps shadow
out of the routing surface as much as possible.

Tier-3 firewall reminder
------------------------
These endpoints only READ from ``research_shadow_decisions``. They
must never write to it (the writer is :mod:`services.research_shadow_logger`)
and must never read from or write to ``paper_trades``,
``crypto_paper_trades``, ``prediction_tracker``, or
``trading_bots[].stats``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from services.auth_helpers import get_current_user
from services.research_shadow_stats import (
    fetch_adaptation_shadow_summary,
    fetch_cost_budget,
    fetch_cost_history,
    fetch_recent_shadow_decisions,
    fetch_regime_stats,
    fetch_shadow_stats,
    fetch_tier_readiness,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/shadow", tags=["research-shadow"])

_db: Any = None


def set_db(database: Any) -> None:
    global _db
    _db = database


def _is_admin(user: dict) -> bool:
    role = (user.get("role") or "").lower()
    return role in ("admin", "owner")


def _require_db() -> None:
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not initialised")


@router.get("/stats")
async def shadow_stats(
    request: Request,
    hours: Optional[int] = None,
    bot_id: Optional[str] = None,
) -> dict:
    """Disagreement-conditional win rate + dissent count + actionable
    flag, per (engine, asset_type) bucket.

    Optional filters:
    * ``hours`` — rolling window
    * ``bot_id`` — drilldown to a single bot's shadow performance

    Returns the maturity guardrail (``min_dissent_samples_required``)
    so the operator UI can render "Need N more dissents to be
    actionable" without re-deriving the threshold.
    """
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await fetch_shadow_stats(_db, hours=hours, bot_id=bot_id)


@router.get("/decisions")
async def shadow_decisions(
    request: Request,
    limit: int = 50,
    bot_id: Optional[str] = None,
    symbol: Optional[str] = None,
    only_dissents: bool = False,
) -> dict:
    """Paginated raw feed of shadow decisions, newest first.

    Backs the per-position drawer in the UI — when an operator
    clicks an open trade row, the drawer shows the timeline of
    cycles + dissents + counterfactual P&L per dissent.

    Hard-cap on ``limit`` at 200 to prevent accidental "limit=10000"
    queries from DOSing the API.
    """
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await fetch_recent_shadow_decisions(
        _db,
        limit=limit,
        bot_id=bot_id,
        symbol=symbol,
        only_dissents=only_dissents,
    )


@router.get("/cost-budget")
async def shadow_cost_budget(request: Request) -> dict:
    """Per-bot rolling 24h LLM spend on shadows, with ceiling-tier
    classification (full / degraded / paused).

    Adversarial-shadow rows always cost $0 (deterministic engine);
    Council-shadow rows accumulate real LLM spend once v2 LLM
    backend is wired. Budget banner uses the ``tier`` field to
    pick the right colour + "paused / degraded" copy.
    """
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await fetch_cost_budget(_db)


@router.get("/cost-history")
async def shadow_cost_history(request: Request, days: int = 14) -> dict:
    """Daily LLM spend history per bot for sparkline rendering.
    Up to 90 days, contiguous day axis (zero-fills inactive days)
    so the sparkline reads as a continuous curve.

    Backs the "LLM Cost Trend" sparkline strip in the admin Shadow
    panel — operator can see at a glance whether a daily budget
    bump is safe before flipping ``COUNCIL_SHADOW_MODE=llm`` for
    steady-state.
    """
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await fetch_cost_history(_db, days=days)


@router.get("/regime-stats")
async def shadow_regime_stats(
    request: Request, hours: Optional[int] = None,
) -> dict:
    """Regime-conditional dissent stats — buckets by
    ``regime_at_decision`` × engine × asset_type, returns per-regime
    win-rate + delta + actionable flag (≥30 dissents per bucket).

    P2 scaffolding for regime-conditional bot weights — the
    instrumentation (``regime_at_decision`` capture on every shadow
    row) is already live; this endpoint exposes the aggregated view
    so the moment regime buckets mature, weights become tunable
    from observed data without a code deploy.
    """
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await fetch_regime_stats(_db, hours=hours)


@router.get("/adaptation-shadow-summary")
async def shadow_adaptation_summary(
    request: Request, days: int = 14,
) -> dict:
    """Quick rollup of what the auto-revert rail WOULD have done in
    shadow mode. Companion to ``ML_ADAPTATION_SHADOW_MODE=true``.

    Lighter than ``GET /api/admin/adaptations/calibration`` (which
    computes percentile distributions for threshold tuning); this
    endpoint returns counts + a recent sample for a small dashboard
    tile.
    """
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await fetch_adaptation_shadow_summary(_db, days=days)


@router.get("/tier-readiness")
async def shadow_tier_readiness(request: Request) -> dict:
    """Single-shot "am I clear to flip Council on yet?" answer.

    Aggregates Adversarial phase, Tier 3 progress, Council bucket
    states, and the env flag into one operator-grade payload.
    Returns ``ready_to_enable_council=true`` only when ALL three
    upstream gates are open. Read-only — no flip button. The
    operator does the flip via .env edit + supervisorctl restart.

    Sees the same Council stats the modulator sees at runtime
    (shared 60s cache), so there's no drift between "what the
    dashboard says is ready" and "what the gate would actually do".
    """
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await fetch_tier_readiness(_db)


@router.get("/regime-weights")
async def shadow_regime_weights(
    request: Request, hours: Optional[int] = None,
) -> dict:
    """Regime-conditional sizing weights derived from shadow dissent
    stats. Default-inert: every (asset_type, regime) bucket without
    ≥30 scored dissents returns ``weight=1.0`` (no-op multiplier).

    Pairs with ``REGIME_WEIGHTS_ENABLED`` env flag — lookup helper
    returns 1.0 when the flag is off, even for actionable buckets.
    Both gates must pass before the multiplier deviates from 1.0.

    P2 scaffolding — bot scheduler hook intentionally NOT wired yet.
    """
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    from services.regime_weights import fetch_regime_weights
    return await fetch_regime_weights(_db, hours=hours)
