"""Sovereign Drift Resolver — score un-traded decisions via market drift
(2026-02-26, P0).

Closes the chicken-and-egg in the Sovereign learning pipeline. Until
this module shipped, the resolution loop in
``sovereign_resolution_loop.py`` could only score sovereign decisions
that had a *fired and closed* paper trade carrying the
``sovereign_decision_id`` foreign key. Two consequences:

1. **HOLD decisions never resolved.** A HOLD never fires a trade by
   definition, so the entire 29% of the corpus that says "stand down"
   was permanently muted — Sovereign never got credit for *correct*
   stand-downs, and never got punished for *wrong* stand-downs.
2. **Non-HOLD decisions only resolved when production gates let the
   paper trader through** (confidence ≥ threshold, Kelly > 0, no
   veto, no symbol-failure cooldown, no quiet-market gate). In quiet
   markets this gate is near-zero, so even confident LONG/SHORT
   opinions died unscored.

Net effect: ``sovereign_promotion_gate`` could never count to 500
resolved rows, and Stage 4 (Sovereign-becomes-primary) was structurally
unreachable.

What this module does
---------------------
For each unresolved sovereign decision that has aged past a horizon
AND has NO matching paper_trade row, fetch the current market price
for the symbol and score the decision against market drift:

* **HOLD** is "right" when ``abs(drift_pct) <= HOLD_DRIFT_TOLERANCE``
  (env-tunable; default 0.5% equity, 1% crypto). A correct HOLD means
  the market didn't make a meaningful move in either direction.
* **LONG** is "right" when ``drift_pct > 0``.
* **SHORT** is "right" when ``drift_pct < 0``.

Requires ``feature_snapshot.entry_price`` to be populated on the
sovereign_decisions row. Legacy rows without that field are skipped
(reason: ``no_entry_price``) — we do not retroactively guess a price.

The resolution loop calls into this module via ``drift_resolve_one``
for every (decision, horizon) pair that the paper-trade-join branch
already declined.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from services.sovereign_ai_core import resolve_sovereign_decision

logger = logging.getLogger(__name__)


def _hold_tolerance(asset_type: str) -> float:
    """How far the market can move before a HOLD is judged wrong."""
    if asset_type == "crypto":
        try:
            return float(os.environ.get(
                "SOVEREIGN_HOLD_DRIFT_TOL_CRYPTO", "0.01",
            ))
        except (TypeError, ValueError):
            return 0.01
    try:
        return float(os.environ.get(
            "SOVEREIGN_HOLD_DRIFT_TOL_EQUITY", "0.005",
        ))
    except (TypeError, ValueError):
        return 0.005


def score_drift(action: str, drift_pct: float, asset_type: str) -> bool:
    """Pure verdict — given an action and realised drift, was the
    decision right?

    Parameters
    ----------
    action
        ``LONG``, ``SHORT``, or ``HOLD``.
    drift_pct
        ``(current_price - entry_price) / entry_price``. Sign-aware.
    asset_type
        ``equity`` or ``crypto`` — determines the HOLD tolerance.
    """
    act = (action or "").strip().upper()
    if act == "LONG":
        return drift_pct > 0
    if act == "SHORT":
        return drift_pct < 0
    if act == "HOLD":
        return abs(drift_pct) <= _hold_tolerance(asset_type)
    # Unknown action — be conservative; don't resolve.
    return False


async def _fetch_current_price(
    market_data: Any, symbol: str, asset_type: str,
) -> Optional[float]:
    """Read the current quote from the market data service. Returns
    ``None`` on any error so the resolver can skip cleanly."""
    try:
        if asset_type == "crypto":
            q = await market_data.get_crypto_quote(symbol)
        else:
            q = await market_data.get_quote(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[sovereign_drift] quote fetch failed sym=%s asset=%s: %s",
            symbol, asset_type, exc,
        )
        return None
    if not q:
        return None
    p = q.get("price") or q.get("last") or q.get("close")
    try:
        p = float(p) if p is not None else None
    except (TypeError, ValueError):
        return None
    if p is None or p <= 0:
        return None
    return p


async def drift_resolve_one(
    db: Any,
    *,
    decision: dict[str, Any],
    horizon: str,
    market_data: Any,
) -> dict[str, Any]:
    """Resolve a single sovereign decision via drift. Returns a
    receipt dict naming the outcome:

      * ``{"status": "resolved", ...}`` on success.
      * ``{"status": "skipped", "reason": "..."}`` on any precondition
        miss. Skip reasons are stable strings so the diagnostic
        endpoint can count them.
    """
    dec_id = decision.get("decision_id")
    action = (decision.get("action") or "").strip().upper()
    asset_type = (decision.get("asset_type") or "equity").strip().lower()
    symbol = (decision.get("symbol") or "").strip().upper()

    if not dec_id:
        return {"status": "skipped", "reason": "no_decision_id"}

    fs = decision.get("feature_snapshot") or {}
    entry_price = fs.get("entry_price")
    try:
        entry_price = float(entry_price) if entry_price is not None else None
    except (TypeError, ValueError):
        entry_price = None
    if entry_price is None or entry_price <= 0:
        return {"status": "skipped", "reason": "no_entry_price"}

    current_price = await _fetch_current_price(
        market_data, symbol, asset_type,
    )
    if current_price is None:
        return {"status": "skipped", "reason": "no_current_price"}

    drift_pct = (current_price - entry_price) / entry_price
    was_right = score_drift(action, drift_pct, asset_type)

    ok = await resolve_sovereign_decision(
        db, dec_id,
        horizon=horizon,
        pnl_pct=drift_pct,
        was_right=was_right,
    )
    if not ok:
        return {"status": "skipped", "reason": "resolve_failed"}

    return {
        "status": "resolved",
        "decision_id": dec_id,
        "horizon": horizon,
        "action": action,
        "entry_price": entry_price,
        "current_price": current_price,
        "drift_pct": round(drift_pct, 6),
        "was_right": was_right,
        "via": "market_drift",
    }


__all__ = ["score_drift", "drift_resolve_one"]
