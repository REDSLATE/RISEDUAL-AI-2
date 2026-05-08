"""Phase 5a — receipt-only ML pipeline wiring.

This module is the single observation hook that wires the new
:class:`RisedualMLPipeline` and :class:`RoadGuardV2` into the legacy
``trading_bot_service.execute_signal`` flow.

It is RECEIPT-ONLY:

  * Calls :meth:`RisedualMLPipeline.decide` against a FeatureFrame
    derived from the live signal + market_data.
  * Calls :meth:`RoadGuardV2.evaluate` with the executor's intent
    plus an :class:`AccountSnapshot` built from open positions.
  * Writes ONE :func:`alpha_decision_log.record_decision` receipt
    capturing the verdict trail.
  * NEVER calls a broker. NEVER places an order. NEVER mutates
    routing.

If anything inside this hook raises, the executor is unaffected —
exceptions are caught and logged; the legacy flow continues.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services import alpha_decision_log
from services.ml.contracts import FeatureFrame, Verdict
from services.ml.pipeline import get_pipeline
from services.ml.roadguard import (
    AccountSnapshot,
    CryptoRoadGuard,
    EquityRoadGuard,
    TradeIntent,
)

logger = logging.getLogger(__name__)


# Closed-loop RG pair: equity intent -> equity RG only;
# crypto intent -> crypto RG only. LANE_MISMATCH at G00 if mis-routed.
_EQUITY_RG = EquityRoadGuard()
_CRYPTO_RG = CryptoRoadGuard()


def _roadguard_for(lane: str):
    """Return the dedicated RG for this lane. Closed loop — there
    is no path that lets an equity intent reach the crypto RG (or
    vice versa) inside this module."""
    if lane == "crypto":
        return _CRYPTO_RG
    return _EQUITY_RG


def _build_feature_frame(
    *,
    signal: Dict[str, Any],
    market_data: Optional[Dict[str, Any]],
    lane: str,
    open_positions: Optional[List[Dict[str, Any]]],
    equity_curve: Optional[List[float]],
) -> FeatureFrame:
    """Synchronous frame skeleton — caller fills market via
    :func:`services.ml.feature_extraction.extract_live_features`
    before passing to the pipeline.
    """
    sig = signal or {}
    if equity_curve and max(equity_curve) > 0:
        dd_pct = abs(min(0.0, equity_curve[-1] - max(equity_curve)) / max(equity_curve) * 100.0)
    else:
        dd_pct = 0.0

    market = dict(market_data or {})
    market.setdefault("dd_pct", dd_pct)

    return FeatureFrame(
        symbol=str(sig.get("symbol") or "UNKNOWN"),
        lane=lane,
        timestamp=datetime.now(timezone.utc).isoformat(),
        market=market,
        extra={
            "intent_hint": (
                "BUY" if str(sig.get("direction", "LONG")).upper() == "LONG"
                else "SELL"
            ),
            "open_positions_count": len(open_positions or []),
        },
    )


def _build_account_snapshot(
    *,
    lane: str,
    open_positions: Optional[List[Dict[str, Any]]],
    bot_capital: float,
) -> AccountSnapshot:
    """Account snapshot from the executor's existing per-call state.

    No broker probe — uses the same open_positions list that the
    legacy roadguard wiring already feeds. ``broker_health_score``
    defaults to 1.0 (healthy) since the executor doesn't currently
    surface a real probe value.
    """
    positions = open_positions or []
    total_exposure = 0.0
    equity_exposure = 0.0
    crypto_exposure = 0.0
    open_in_lane = 0
    existing_symbols: List[str] = []

    for pos in positions:
        size = float(pos.get("size_usd") or 0.0)
        total_exposure += size
        pos_lane = (pos.get("lane") or "").lower()
        if not pos_lane:
            asset_type = (pos.get("asset_type") or "").lower()
            pos_lane = "crypto" if asset_type == "crypto" else "equity"
        if pos_lane == "crypto":
            crypto_exposure += size
        else:
            equity_exposure += size
        if pos_lane == lane:
            open_in_lane += 1
        sym = pos.get("symbol")
        if sym:
            existing_symbols.append(str(sym))

    # Bot capital is the closest proxy we have for cash + equity in
    # the legacy executor; use it for both.
    cap = max(0.0, float(bot_capital or 0.0))
    return AccountSnapshot(
        cash_usd=cap,
        equity_value_usd=cap,
        daily_realized_pnl_usd=0.0,
        broker_health_score=1.0,
        total_exposure_usd=total_exposure,
        equity_exposure_usd=equity_exposure,
        crypto_exposure_usd=crypto_exposure,
        open_positions_total=len(positions),
        open_positions_in_lane=open_in_lane,
        existing_open_symbols=existing_symbols,
    )


async def run_shadow_pipeline(
    db,
    *,
    signal: Dict[str, Any],
    market_data: Optional[Dict[str, Any]],
    lane: str,
    requested_notional_usd: float,
    open_positions: Optional[List[Dict[str, Any]]],
    equity_curve: Optional[List[float]],
    bot_capital: float,
) -> Optional[Dict[str, Any]]:
    """Run the receipt-only Phase 5a chain. NEVER raises.

    Returns a small dict summarising the verdicts for the caller's
    diagnostics, or None on any failure. The caller's routing must
    not depend on the return value — receipts are the value.
    """
    try:
        frame = _build_feature_frame(
            signal=signal,
            market_data=market_data,
            lane=lane,
            open_positions=open_positions,
            equity_curve=equity_curve,
        )
        # Fill in live data wherever the caller didn't pre-supply.
        try:
            from services.ml.feature_extraction import extract_live_features
            live = await extract_live_features(
                symbol=frame.symbol, lane=lane, db=db, base=frame.market,
            )
            frame.market = live
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ml.phase5a] live feature extraction failed: %s", exc)

        pipeline = get_pipeline()
        decision = pipeline.decide(frame)

        # RoadGuard v2 closed-loop evaluation. ALWAYS run in shadow
        # mode — even if the pipeline already blocked, the operator
        # wants to see what the dedicated RG WOULD have done. The
        # `will_hit_live_broker` flag is hard-coded False here so
        # the kill-switch (G10) only fires if both an enforce flip
        # AND a real broker call were attempted (neither happens
        # in shadow wiring).
        rg_verdict = None
        try:
            snapshot = _build_account_snapshot(
                lane=lane,
                open_positions=open_positions,
                bot_capital=bot_capital,
            )
            # Use the pipeline's final decision when we have one,
            # else fall back to BUY/SELL inferred from the signal.
            if decision.final.decision in (Verdict.BUY.value, Verdict.SELL.value):
                side = decision.final.decision
            else:
                side = (
                    "BUY" if str(signal.get("direction", "LONG")).upper() == "LONG"
                    else "SELL"
                )
            intent = TradeIntent(
                symbol=frame.symbol,
                lane=lane,
                side=side,
                requested_notional_usd=float(requested_notional_usd),
                will_hit_live_broker=False,  # receipt-only — never live
            )
            rg = _roadguard_for(lane)
            rg_verdict = rg.evaluate(intent, snapshot)
            # Persist to the LANE-SPECIFIC collection. Closed-loop
            # observability: equity verdicts go to roadguard_equity_decisions,
            # crypto verdicts go to roadguard_crypto_decisions.
            if db is not None:
                try:
                    await db[rg.DECISIONS_COLLECTION].insert_one({
                        "created_at": datetime.now(timezone.utc),
                        "symbol": frame.symbol,
                        "lane": lane,
                        "pipeline_blocked_at": decision.blocked_at,
                        "verdict": rg_verdict.as_dict(),
                        "schema_version": 1,
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[ml.phase5a] %s log insert failed: %s",
                        rg.DECISIONS_COLLECTION, exc,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ml.phase5a] roadguard_v2 eval failed: %s", exc)

        # ── Phase 5b — broker wire (4-gate defense in depth) ──
        # Always called. Always shadow-only by default — every gate
        # defaults closed. Persists a phase5b_intents row that the
        # operator reviews before promoting.
        try:
            from services.ml.broker_wire import run_broker_wire
            await run_broker_wire(
                db,
                lane=lane,
                symbol=frame.symbol,
                side=(
                    decision.final.decision
                    if decision.final.decision in (Verdict.BUY.value, Verdict.SELL.value)
                    else (
                        "BUY" if str(signal.get("direction", "LONG")).upper() == "LONG"
                        else "SELL"
                    )
                ),
                notional_usd=float(requested_notional_usd),
                pipeline_decision=decision,
                rg_verdict=rg_verdict,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ml.phase5b] broker_wire failed (degrading): %s", exc)

        # Adjust the receipt's blocked_at if RoadGuard would have
        # blocked the otherwise-approved trade. Receipts capture the
        # FIRST gate that would have stopped the trade — so a
        # RoadGuard BLOCK on a pipeline PASS becomes blocked_at=roadguard.
        receipt_blocked_at = decision.blocked_at
        receipt_reason = decision.final.reason
        if (
            decision.blocked_at is None
            and rg_verdict is not None
            and rg_verdict.decision == "BLOCK"
        ):
            receipt_blocked_at = "roadguard"
            receipt_reason = rg_verdict.reason

        # Write ONE receipt capturing the chain.
        is_approved = (
            receipt_blocked_at is None
            and decision.final.decision in (Verdict.BUY.value, Verdict.SELL.value)
        )
        await alpha_decision_log.record_decision(
            db,
            symbol=frame.symbol,
            lane=lane,
            decision="APPROVED" if is_approved else "NO_TRADE",
            blocked_at=receipt_blocked_at,
            reason=receipt_reason,
            confidence=decision.final.confidence,
            trail=[v.as_dict() for v in decision.trail],
            extras={
                "final_decision": decision.final.decision,
                "final_layer": decision.final.layer,
                "roadguard_v2": rg_verdict.as_dict() if rg_verdict else None,
                "requested_notional_usd": requested_notional_usd,
            },
        )
        return {
            "ml_pipeline_blocked_at": decision.blocked_at,
            "ml_pipeline_reason": decision.final.reason,
            "ml_pipeline_decision": decision.final.decision,
            "ml_pipeline_confidence": decision.final.confidence,
            "roadguard_v2": rg_verdict.as_dict() if rg_verdict else None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ml.phase5a] shadow pipeline failed (degrading): %s", exc)
        return None
