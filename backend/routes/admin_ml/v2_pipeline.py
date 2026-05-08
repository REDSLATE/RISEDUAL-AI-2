"""Synthetic-frame pipeline dry-run.

Single endpoint at ``POST /api/admin/ml/v2/pipeline/decide``.
NEVER places an order. NEVER calls a broker. Optionally records a
receipt to ``alpha_decision_log`` if ``?record=true`` is passed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import Body, HTTPException, Request

from services import alpha_decision_log
from services.ml.contracts import FeatureFrame
from services.ml.pipeline import get_pipeline
from services.ml.roadguard import AccountSnapshot, RoadGuardV2, TradeIntent

from ._auth import require_admin
from ._routers import get_db, router


@router.post("/pipeline/decide")
async def pipeline_decide(
    request: Request,
    payload: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    """Dry-run the 8-ML pipeline against an operator-supplied frame.

    Body shape:
      {
        "symbol": "AAPL",
        "lane": "equity",
        "market": { ... },                    // any of the 21 fields
        "intent_hint": "BUY"                  // optional, default BUY
      }

    NEVER places an order. NEVER calls a broker. Optionally writes a
    receipt to alpha_decision_log if ?record=true is passed.
    """
    await require_admin(request)

    symbol = str(payload.get("symbol") or "TEST").upper()
    lane = str(payload.get("lane") or "equity").lower()
    if lane not in ("equity", "crypto"):
        raise HTTPException(status_code=400, detail="lane must be equity|crypto")

    frame = FeatureFrame(
        symbol=symbol,
        lane=lane,
        timestamp=datetime.now(timezone.utc).isoformat(),
        market=dict(payload.get("market") or {}),
        extra={"intent_hint": str(payload.get("intent_hint") or "BUY").upper()},
    )
    decision = get_pipeline().decide(frame)

    rg_verdict = None
    if "roadguard" in payload:
        rg_payload = payload["roadguard"] or {}
        try:
            snapshot = AccountSnapshot(**(rg_payload.get("snapshot") or {}))
            intent = TradeIntent(
                symbol=symbol, lane=lane,
                side=decision.final.decision,
                requested_notional_usd=float(rg_payload.get("requested_notional_usd", 100.0)),
                will_hit_live_broker=False,
            )
            rg_verdict = RoadGuardV2().evaluate(intent, snapshot)
        except Exception as exc:  # noqa: BLE001
            return {
                "pipeline": decision.as_dict(),
                "roadguard_error": f"{type(exc).__name__}:{exc}",
            }

    db = get_db()
    record = bool(request.query_params.get("record"))
    if record and db is not None:
        await alpha_decision_log.record_pipeline_decision(db, decision)

    return {
        "pipeline": decision.as_dict(),
        "roadguard_v2": rg_verdict.as_dict() if rg_verdict else None,
        "recorded": record,
    }
