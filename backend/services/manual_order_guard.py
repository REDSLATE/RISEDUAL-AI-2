"""Manual order route helper for the Patent J/K/M/I guard pipeline.

The crypto bot synthesises Bull/Bear/Commander from its own adversarial
layer. Manual orders don't have that — a user clicking "Buy 100 AAPL"
is a single conviction signal. We synthesise a one-sided agent pair
from the order's intent (Bull strong + Bear weak for buys; symmetric
for sells), so Patent K's enforcement still applies (e.g. zero-notional
orders are filtered by HOLD_NOT_PROMOTED) and the proof chain logs the
full lifecycle for compliance.

This helper is a thin async wrapper around
``run_guarded_decision_pipeline_async``. Routes call it with the
order's symbol/side/notional and the user's role; the helper builds
all the telemetry plumbing.
"""
from __future__ import annotations

__domain__ = "DTD"

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def run_manual_order_guard(
    *,
    db: Any,
    user: dict,
    asset_class: str,
    symbol: str,
    side: str,
    base_notional: float,
    requested_multiplier: Optional[float] = None,
    context: Optional[dict] = None,
) -> dict:
    """Run the full Patent guard for a manual order.

    Returns ``{"allow": True, "notional": ..., "proof_hashes": [...], "reasons": [...]}``
    or ``{"allow": False, "reason_code": "...", "message": "...", "proof_hashes": [...]}``.

    Behaviour when ``PATENT_GUARD_ENABLED=0``: skips the guard
    entirely and returns ``{"allow": True, "notional": base_notional}``.
    """
    if (os.environ.get("PATENT_GUARD_ENABLED", "1") or "").lower() in ("0", "false", ""):
        return {"allow": True, "notional": base_notional, "skipped": True}

    if base_notional <= 0:
        return {"allow": True, "notional": 0.0, "skipped": True}

    try:
        from services.adversarial_enforcer import AgentDecision
        from services.failure_mode_classifier import MarketTelemetry, ModelTelemetry
        from services.proof_chain import AsyncMongoProofChainStore
        from services.decision_pipeline_guard import run_guarded_decision_pipeline_async
        from services.risk_budget_gateway import (
            build_track_record_for_user,
            get_daily_realized_loss,
            mint_authority_for_user,
        )
        from services.authority_risk_budget import RiskBudgetRequest

        user_id = str(user.get("_id") or user.get("id") or "anon")
        side_upper = str(side).upper()
        is_buy = side_upper in ("BUY", "LONG")
        is_sell = side_upper in ("SELL", "SHORT")
        if not (is_buy or is_sell):
            # Non-directional side (e.g. CLOSE) — guard is a pass-through.
            return {"allow": True, "notional": base_notional, "skipped": True}

        authority = mint_authority_for_user(user, asset_class)
        track = await build_track_record_for_user(user_id, asset_class)
        daily_loss = await get_daily_realized_loss(
            asset_class=asset_class, user_id=user_id,
        )

        # User-driven orders are single-conviction. We synthesise a
        # high-conviction Bull (for buys) or Bear (for sells) plus a
        # low-conviction opposite agent so K's dissent rule still
        # rules out near-coin-flip orders. Manual users get a small
        # baseline confidence boost (0.85) since they've explicitly
        # clicked the button — they're not low-conviction by nature.
        manual_conf = 0.85
        weak_conf = 0.20
        bull = AgentDecision(
            name="user_intent",
            action="BUY" if is_buy else "HOLD",
            confidence=manual_conf if is_buy else weak_conf,
            reasons=["manual_user_intent"],
        )
        bear = AgentDecision(
            name="user_intent",
            action="SELL" if is_sell else "HOLD",
            confidence=manual_conf if is_sell else weak_conf,
            reasons=["manual_user_intent"],
        )

        # Manual orders try to use real telemetry baselines for the
        # symbol — Patent M's volatility/liquidity branches activate
        # only when we have at least 3 historical samples. Falls back
        # to zeros (dormant branches) when the symbol is new.
        from services.equity_telemetry import get_telemetry as _get_eq_tel
        market_tel = await _get_eq_tel(
            symbol,
            asset_class=asset_class,
        ) or MarketTelemetry(
            symbol=symbol.upper(),
            asset_type=asset_class,
            atr_pct=0.0, atr_pct_baseline=0.0,
            volume_zscore=0.0, spread_bps=0.0, spread_bps_baseline=0.0,
        )
        model_tel = ModelTelemetry(
            calibration_gap=float(track.calibration_gap or 0.0),
            prediction_entropy=0.0,
            confidence=manual_conf,
            confidence_baseline=0.65,
            disagreement_score=abs(bull.confidence - bear.confidence),
            recent_error_rate=max(0.0, 1.0 - (track.win_rate or 0.0)),
            loss_streak=int(track.loss_streak or 0),
            max_drawdown=float(track.max_drawdown or 0.0),
        )

        risk_req = RiskBudgetRequest(
            action="BUY" if is_buy else "SELL",
            base_notional=float(base_notional),
            base_multiplier=1.0,
            authority=authority,
            track_record=track,
            daily_realized_loss=daily_loss,
            requested_multiplier=requested_multiplier,
        )

        proof_store = AsyncMongoProofChainStore(db) if db is not None else None
        from datetime import datetime, timezone

        guard = await run_guarded_decision_pipeline_async(
            entity_id=f"{asset_class}:{user_id}:{symbol.upper()}:{int(datetime.now(timezone.utc).timestamp())}",
            bull=bull, bear=bear, commander=None,
            market=market_tel, model=model_tel,
            risk_request=risk_req,
            proof_store=proof_store,
            actor=f"user:{user_id}",
        )

        # ── Shadow mode (Step 5 of the rollout plan) ─────────────────
        # When ``GUARD_SHADOW_MODE=1`` the guard runs and writes proof
        # chain + a shadow_log entry, but its verdict is NOT enforced.
        # Routes proceed with the original notional. Lets operators
        # compare "would have blocked" vs "actually executed" before
        # flipping the guard into enforcement mode.
        from services.guard_shadow_log import (
            is_shadow_mode_enabled,
            log_guard_shadow_decision,
        )
        if is_shadow_mode_enabled():
            await log_guard_shadow_decision(
                db,
                source=f"manual_order:{(context or {}).get('route', 'unknown')}",
                entity_id=f"{asset_class}:{user_id}:{symbol.upper()}",
                guard_result=guard,
                executed_action=str(side).upper(),
                executed_notional=float(base_notional),
                context={
                    "asset_class": asset_class,
                    "symbol": symbol.upper(),
                    "user_id": user_id,
                    **(context or {}),
                },
            )
            return {
                "allow": True,
                "notional": base_notional,
                "shadow": True,
                "would_allow": bool(guard.get("allow")),
                "would_notional": float(guard.get("notional") or 0.0),
                "proof_hashes": guard.get("proof_hashes", []),
                "reasons": guard.get("reasons", []),
            }

        if not guard["allow"]:
            return {
                "allow": False,
                "reason_code": guard["reasons"][0] if guard["reasons"] else "blocked",
                "message": "Patent guard blocked this order: "
                           + ", ".join(guard["reasons"][:3]),
                "proof_hashes": guard["proof_hashes"],
                "reasons": guard["reasons"],
            }

        return {
            "allow": True,
            "notional": float(guard["notional"]),
            "risk_multiplier": float(guard["risk_multiplier"]),
            "proof_hashes": guard["proof_hashes"],
            "reasons": guard["reasons"],
        }
    except Exception as e:  # noqa: BLE001 — fail-open is intentional
        # for manual orders. The user clicked the button; we don't
        # want a guard-side bug to block a legit trade. Log loudly so
        # observability catches the issue.
        logger.warning("[manual_guard] pipeline error, falling open: %s", e)
        return {"allow": True, "notional": base_notional, "skipped": True, "error": str(e)}
