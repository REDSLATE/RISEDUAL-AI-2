"""Active Sovereign gate — bridges Core v2 to Alpha's EXISTING shadow (2026-06).

Doctrine (operator directive): do NOT stand up a second shadow brain. This gate
consults Alpha's already-running deterministic learner
(`sovereign.wild_adaptive_core_v2.run_adaptive_core`) using the brain's LEARNED
weights from the existing `sovereign.local_state.LocalState`
(``/app/data/sovereign/alpha/state.json``), and records each decision back into
that SAME LocalState decision log. Outcomes continue to flow through the
existing `services.sovereign_outcome_bridge` when trades resolve — this module
never opens a parallel outcome store.

Real data only: features come from `market_data_pool` (quote + technical
indicators). Missing technicals collapse to a low-confidence HOLD; nothing is
fabricated.

The gate is ADVISORY. It returns a proposal with ``.action`` / ``.vetoes`` that
the engine reads; it can only VETO (never submit), and the engine only honors a
veto when ``sovereign_enforce`` AND ``live`` both hold.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from services.alpha_core_v2.contracts import Candidate

logger = logging.getLogger(__name__)

_BRAIN = (os.environ.get("ALPHA_BRAIN_NAME") or "alpha").strip()
_DEFAULT_STATE = "/app/data/sovereign/alpha/state.json"


@dataclass(frozen=True)
class ShadowProposal:
    """Engine-facing proposal. Duck-typed to what `engine._process` reads:
    ``.action`` (str, "BUY" only means proceed) and ``.vetoes`` (list[str])."""
    action: str
    vetoes: list = field(default_factory=list)
    support_score: float = 0.0
    reason: str = ""


def _state_path() -> str:
    return (os.environ.get("SOVEREIGN_STATE_PATH") or "").strip() or _DEFAULT_STATE


def _load_weights() -> dict:
    """Read the brain's LEARNED weights from the existing shadow (READ-ONLY).
    Falls back to Alpha's identity default weights if the state file is
    absent/empty. Never raises — a missing shadow just means default weights.

    We deliberately never WRITE to this file: the crypto sidecar owns its
    learning loop and rewrites it every tick, so a second writer from another
    process would clobber the sidecar's state. The V2 shadow verdict is
    recorded on the V2 side (engine structured log + receipt), not here."""
    try:
        from sovereign.local_state import LocalState
        from sovereign.wild_adaptive_core_v2 import default_weights
        st = LocalState(brain=_BRAIN, path=_state_path(), mode="DTD")
        return dict(st.weights) if st.weights else default_weights()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[shadow] weight load failed (%s); using defaults", exc)
        try:
            from sovereign.wild_adaptive_core_v2 import default_weights
            return default_weights()
        except Exception:  # noqa: BLE001
            return {"trend": 0.85, "macd": 0.65, "rsi": -0.25}


async def _build_topofbook(candidate: Candidate) -> dict:
    """Real top-of-book for the adaptive core: price + technicals. Uses the
    discovery mark as price and enriches with Alpha Vantage technicals. Missing
    fields are left absent so `extract_features` applies its neutral defaults."""
    price = float(candidate.mark or 0.0)
    technicals: dict = {}
    try:
        from services import market_data_pool as mdp
        if price <= 0:
            q = await mdp.market_quote(candidate.symbol)
            if q:
                price = float(q.get("price") or q.get("last") or 0.0)
        ti = await mdp.get_technical_indicators(candidate.symbol)
        if isinstance(ti, dict):
            if ti.get("rsi_14") is not None:
                technicals["rsi14"] = float(ti["rsi_14"])
            if ti.get("macd") is not None:
                technicals["macd"] = float(ti["macd"])
            if ti.get("sma_20") is not None:
                technicals["sma20"] = float(ti["sma_20"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[shadow] technicals fetch failed %s: %s", candidate.symbol, exc)
    return {"symbol": candidate.symbol, "price": price, "technicals": technicals}


class ShadowGate:
    """Callable gate. `await gate(candidate) -> ShadowProposal`."""

    def __init__(self, weights: Optional[dict] = None) -> None:
        # Weights are read once per gate build (worker rebuilds the gate each
        # tick via the engine factory, so learned-weight updates are picked up).
        self._weights = weights if weights is not None else _load_weights()

    async def __call__(self, candidate: Candidate) -> ShadowProposal:
        from sovereign.wild_adaptive_core_v2 import run_adaptive_core
        top = await _build_topofbook(candidate)
        decision = run_adaptive_core(top, self._weights)
        if decision.action == "BUY":
            return ShadowProposal(action="BUY", vetoes=[],
                                  support_score=float(decision.confidence),
                                  reason="shadow_bullish")
        # SELL / HOLD are both non-entries for long-only Core v2 → veto.
        vetoes = [f"shadow_{decision.action.lower()}"]
        if decision.confidence < 0.25:
            vetoes.append("shadow_low_confidence")
        return ShadowProposal(action="HOLD", vetoes=vetoes,
                              support_score=float(decision.confidence),
                              reason="shadow_non_bullish")


def build_gate(db: Any = None):
    """Factory used by the worker. Selects the ACTIVE gate.

    Default: the existing-shadow gate (`ShadowGate`). Set
    ``ALPHA_SOVEREIGN_MODEL=rise`` to swap in the standalone RISE model
    (`sovereign.py`) — that path needs a real options/news/R:R feature builder
    and will HOLD until one is supplied, so it stays opt-in for the future
    RISE-primary phase.
    """
    model = (os.environ.get("ALPHA_SOVEREIGN_MODEL") or "shadow").strip().lower()
    if model == "rise":
        from services.alpha_core_v2.sovereign_bridge import SovereignGate
        from services.alpha_core_v2.sovereign_shadow_features import build_sovereign_features
        return SovereignGate(build_sovereign_features)
    return ShadowGate()


__all__ = ["ShadowGate", "ShadowProposal", "build_gate"]
