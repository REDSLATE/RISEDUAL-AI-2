"""Shadow engines — what each engine "would have done" on a given cycle.

Two implementations live here:

* :func:`run_adversarial_shadow` — wraps the existing
  :func:`services.adversarial_core.run_adversarial_decision` so the
  Bull/Bear/Commander stack can ride along as a shadow. Deterministic,
  zero LLM cost.

* :func:`run_council_shadow` — v1 implementation: a deterministic
  3-rule consensus (RSI, MACD-direction, volume confirmation). Produces
  a Council-shaped output without any LLM call. Naming preserves the
  upgrade path for v2 (real multi-LLM consensus via
  :mod:`services.multi_model_hypothesis_service`) once cost
  monitoring is proven in production.

Why v1 Council is rule-based, not LLM-backed
--------------------------------------------
Cost monitoring (``research_shadow.should_fire_shadow`` cost ceiling
tiers) is the gating-mechanism between v1 and v2. Until we have
real-world LLM-spend data per cycle, we keep Council fast, cheap,
and deterministic. The plumbing — engine wrapper, dissent detection,
deferred scoring — is identical for v1 and v2; only the
:func:`run_council_shadow` body changes.

Engine output shape
-------------------
Every engine returns a small dict::

    {
        "action": "LONG" | "SHORT" | "HOLD" | "CLOSE",
        "thesis": str,              # short, human-readable
        "confidence": float,        # 0.0 - 1.0
        "llm_cost_usd": float,      # 0.0 for deterministic engines
    }

The caller (``fire_shadow`` in this module) wraps it into a
:class:`ShadowDecision` and persists it. Engines must not raise —
they catch their own exceptions and return a neutral HOLD.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from services.research_shadow import (
    ENGINE_ADVERSARIAL,
    ENGINE_COUNCIL,
    ENGINE_NONE,
    FILL_COST_BPS,
    VALID_SHADOW_ENGINES,
    ShadowDecision,
    canonicalise_action,
    should_fire_shadow,
)
from services.research_shadow_logger import (
    get_daily_cost_usd,
    get_last_shadow_ts,
    insert_shadow_decision,
)

logger = logging.getLogger(__name__)


# ── Adversarial shadow ────────────────────────────────────────────────────────


async def run_adversarial_shadow(
    db: Any, signal: dict[str, Any],
) -> dict[str, Any]:
    """Run the existing adversarial decision core in shadow mode.

    Calls into :func:`services.adversarial_core.run_adversarial_decision`
    with the SAME signal the active path would see. Returns the
    canonical engine-output dict.

    Note on double-gating: the real adversarial core is itself
    double-gated (CRYPTO_ADVERSARIAL_ENABLED env flag + ML Tier 3
    unlock). When either gate is shut, the core returns None and we
    log a HOLD shadow with a thesis explaining why — that way the
    operator can see in the timeline that the shadow engine was
    asked but had no opinion.
    """
    try:
        from services.adversarial_core import run_adversarial_decision
        decision = await run_adversarial_decision(db, signal)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[shadow-adversarial] core call failed: %s", exc)
        return {
            "action": "HOLD",
            "thesis": "adversarial_core_error",
            "confidence": 0.0,
            "llm_cost_usd": 0.0,
        }

    if decision is None:
        return {
            "action": "HOLD",
            "thesis": "adversarial_gates_closed",
            "confidence": 0.0,
            "llm_cost_usd": 0.0,
        }

    raw_action = (decision.get("decision") or "HOLD").upper()
    return {
        "action": canonicalise_action(raw_action),
        "thesis": (
            f"phase={decision.get('phase')} "
            f"edge_gap={decision.get('edge_gap'):.3f} "
            f"risk_mult={decision.get('risk_multiplier'):.2f}"
        ),
        "confidence": float(decision.get("risk_multiplier") or 0.0),
        "llm_cost_usd": 0.0,
    }


# ── Council shadow (v1: rule-based) ───────────────────────────────────────────


def _rsi_vote(signal: dict[str, Any]) -> tuple[str, str]:
    """RSI sub-rule. Long below 30, short above 70, hold in between."""
    rsi = signal.get("rsi")
    if rsi is None:
        return "HOLD", "rsi_missing"
    rsi_f = float(rsi)
    if rsi_f < 30:
        return "LONG", f"rsi_oversold_{rsi_f:.1f}"
    if rsi_f > 70:
        return "SHORT", f"rsi_overbought_{rsi_f:.1f}"
    return "HOLD", f"rsi_neutral_{rsi_f:.1f}"


def _momentum_vote(signal: dict[str, Any]) -> tuple[str, str]:
    """5-bar momentum sub-rule. Long if positive, short if negative,
    hold within 0.2% noise band.
    """
    mom = signal.get("momentum_5b")
    if mom is None:
        return "HOLD", "momentum_missing"
    mom_f = float(mom)
    # Momentum is expressed as a fraction (0.005 = 0.5%).
    if mom_f > 0.002:
        return "LONG", f"momentum_up_{mom_f * 100:.2f}pct"
    if mom_f < -0.002:
        return "SHORT", f"momentum_down_{mom_f * 100:.2f}pct"
    return "HOLD", f"momentum_flat_{mom_f * 100:.2f}pct"


def _volume_vote(signal: dict[str, Any]) -> tuple[str, str]:
    """Volume sub-rule. Confirms direction when volume_ratio >= 1.2.
    On its own, abstains (returns HOLD) — it's a multiplier on
    direction, not a direction itself. Combined with RSI/momentum
    in :func:`run_council_shadow`.
    """
    vol = signal.get("volume_ratio")
    if vol is None:
        return "HOLD", "volume_missing"
    vol_f = float(vol)
    if vol_f >= 1.2:
        return "CONFIRM", f"volume_strong_{vol_f:.2f}x"
    return "HOLD", f"volume_weak_{vol_f:.2f}x"


async def run_council_shadow(
    db: Any, signal: dict[str, Any],  # noqa: ARG001
) -> dict[str, Any]:
    """Council shadow v1: deterministic 3-rule consensus.

    Rules:
        1. RSI direction (oversold→LONG, overbought→SHORT)
        2. 5-bar momentum direction
        3. Volume confirmation (multiplier, not direction)

    Council fires LONG / SHORT only when RSI and momentum AGREE.
    Volume confirmation boosts confidence but isn't required —
    a council that needs all three to agree fires too rarely to
    produce useful dissent volume.

    Confidence formula: 0.5 baseline when RSI+momentum agree,
    +0.2 if volume confirms. Caps at 0.7. Deliberately conservative
    — high-confidence Council calls are reserved for v2 (LLM-backed).
    """
    rsi_dir, rsi_thesis = _rsi_vote(signal)
    mom_dir, mom_thesis = _momentum_vote(signal)
    vol_dir, vol_thesis = _volume_vote(signal)

    # Both directional voters must agree on a non-HOLD direction.
    if rsi_dir == "HOLD" or mom_dir == "HOLD" or rsi_dir != mom_dir:
        return {
            "action": "HOLD",
            "thesis": f"council_no_consensus | {rsi_thesis} | {mom_thesis}",
            "confidence": 0.0,
            "llm_cost_usd": 0.0,
        }

    confidence = 0.5
    if vol_dir == "CONFIRM":
        confidence = 0.7
    return {
        "action": rsi_dir,
        "thesis": f"council_consensus_{rsi_dir} | {rsi_thesis} | {mom_thesis} | {vol_thesis}",
        "confidence": confidence,
        "llm_cost_usd": 0.0,  # v2 will populate this with real LLM spend
    }


# ── Engine dispatcher + fire helper ───────────────────────────────────────────


async def _run_engine(
    engine: str, db: Any, signal: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch to the requested engine. Defensive default = HOLD."""
    if engine == ENGINE_ADVERSARIAL:
        return await run_adversarial_shadow(db, signal)
    if engine == ENGINE_COUNCIL:
        return await run_council_shadow(db, signal)
    return {
        "action": "HOLD",
        "thesis": f"unknown_engine_{engine}",
        "confidence": 0.0,
        "llm_cost_usd": 0.0,
    }


async def fire_shadow(
    db: Any,
    *,
    bot_id: str,
    user_id: str,
    symbol: str,
    asset_type: str,
    decision_phase: str,
    active_engine: str,
    active_action: str,
    shadow_engine: str,
    signal: dict[str, Any],
    mid_price: float,
    trade_id: Optional[str] = None,
    active_trade_doc_id: Optional[str] = None,
    shadow_paused: bool = False,
) -> Optional[str]:
    """Fire a shadow on this cycle, gated by ``should_fire_shadow``.

    Returns the persisted ``decision_id`` on success, ``None`` if
    the gate blocked us or persistence failed. Caller should treat
    None as "shadow not recorded this cycle, no big deal" — never
    raise out from here, the active trade path must continue
    regardless.

    This function is intentionally wrapped in a broad try/except at
    the top level: ANY exception is swallowed to a logged warning.
    A shadow logging bug must NEVER take down a live bot.
    """
    try:
        if shadow_engine == ENGINE_NONE or shadow_engine not in VALID_SHADOW_ENGINES:
            return None

        # Per-bot kill switch — admin can pause a bot's shadow without
        # editing the engine type. Useful for "council is fine, just
        # quiet it for 24h while we burn through some compute budget".
        if shadow_paused:
            return None

        # Gate: rate limit + cost ceiling for LLM engines.
        last_ts = await get_last_shadow_ts(db, bot_id)
        daily_cost = await get_daily_cost_usd(db, bot_id)
        fire, reason = should_fire_shadow(
            shadow_engine=shadow_engine,
            last_shadow_ts=last_ts,
            daily_cost_usd=daily_cost,
            decision_phase=decision_phase,
        )
        if not fire:
            logger.debug(
                "[shadow] skipped fire: bot=%s engine=%s reason=%s",
                bot_id, shadow_engine, reason,
            )
            return None

        # Run the shadow engine. Engines never raise — they return a
        # neutral HOLD on any internal error.
        out = await _run_engine(shadow_engine, db, signal)

        # Resolve fill cost from asset_type. Defaults pinned in
        # research_shadow.FILL_COST_BPS.
        fill_bps = FILL_COST_BPS.get(asset_type, FILL_COST_BPS["stock"])

        # Capture volume_ratio so the scorer can later apply
        # volume-conditional fill costs without re-querying the tape.
        # Stays None if the signal didn't carry it (e.g. some equity
        # signals don't compute it).
        vol_ratio = signal.get("volume_ratio") if isinstance(signal, dict) else None

        decision = ShadowDecision(
            bot_id=bot_id,
            user_id=user_id,
            symbol=symbol,
            asset_type=asset_type,
            decision_phase=decision_phase,
            active_engine=active_engine,
            active_action=active_action,
            shadow_engine=shadow_engine,
            shadow_action=out.get("action") or "HOLD",
            shadow_thesis=out.get("thesis"),
            shadow_confidence=out.get("confidence"),
            mid_price=float(mid_price or 0.0),
            sim_fill_bps_round_trip=fill_bps,
            llm_cost_usd=float(out.get("llm_cost_usd") or 0.0),
            volume_ratio_at_decision=(
                float(vol_ratio) if isinstance(vol_ratio, (int, float)) else None
            ),
            trade_id=trade_id,
            active_trade_doc_id=active_trade_doc_id,
        )
        return await insert_shadow_decision(db, decision)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[shadow] fire_shadow swallowed exception bot=%s symbol=%s: %s",
            bot_id, symbol, exc,
        )
        return None
