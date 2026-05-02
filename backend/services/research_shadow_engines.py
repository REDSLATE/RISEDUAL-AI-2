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
    should_skip_cycle_for_agreement_run,
)
from services.research_shadow_logger import (
    count_recent_agreement_run,
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
    """Council shadow — multi-LLM weighted consensus.

    Routes to either v1 (deterministic 3-rule) or v2 (LLM-backed 3-model
    consensus) based on the env flag ``COUNCIL_SHADOW_MODE``:

    * ``rule`` (default) — :func:`_run_council_rule_based`. Zero LLM cost,
      fast, useful while v2 cost monitoring proves the budget envelope.
    * ``llm`` — :func:`_run_council_llm`. Three-model weighted consensus
      (GPT-5.2 + Claude Sonnet 4.5 + Gemini 2.5 Flash). Costs are
      captured per call and bubbled up via ``llm_cost_usd`` so the
      cost-ceiling tiering in :func:`should_fire_shadow` can pause the
      engine before the daily cap is exceeded.

    The router lives here, not in fire_shadow, so that a future v3
    Council variant slots in without touching the engine dispatcher.
    """
    import os as _os
    mode = _os.environ.get("COUNCIL_SHADOW_MODE", "rule").lower()
    if mode == "llm":
        return await _run_council_llm(signal)
    return await _run_council_rule_based(signal)


async def _run_council_rule_based(signal: dict[str, Any]) -> dict[str, Any]:
    """Council v1: deterministic 3-rule consensus.

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


# ── Council v2: LLM-backed multi-model consensus ──────────────────────────────


# Lightweight per-cycle cost estimates per provider, in USD per call,
# for a ~200-token prompt + ~80-token JSON response. Used purely for
# `llm_cost_usd` accounting on the shadow row — the cost-ceiling
# tiering in `should_fire_shadow` reads the rolling 24h sum of this
# field. Numbers are conservative upper bounds; real spend tracked
# by the universal-key system independently.
_LLM_COUNCIL_COST_USD: dict[str, float] = {
    "openai": 0.005,
    "anthropic": 0.005,
    "gemini": 0.001,
}

# Per-model weight for the consensus tally. Sum to ~1.0 — math
# normalises in `_council_llm_consensus` so the absolute values
# don't matter, only the ratios.
_LLM_COUNCIL_WEIGHTS: dict[str, float] = {
    "openai": 0.40,
    "anthropic": 0.40,
    "gemini": 0.20,
}


_COUNCIL_SYSTEM_PROMPT = (
    "You are a quantitative trading consensus engine. Given a snapshot "
    "of technical indicators, return a single JSON decision. Do not "
    "deliberate, do not explain at length, return JSON only.\n\n"
    "Schema:\n"
    "{\n"
    '  "action": "LONG" | "SHORT" | "HOLD",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "reason": "one-sentence rationale, max 100 chars"\n'
    "}\n\n"
    "Rules:\n"
    "- Default to HOLD when signals are mixed.\n"
    "- LONG only when momentum + RSI + volume align bullishly.\n"
    "- SHORT only when momentum + RSI align bearishly with volume confirmation.\n"
    "- confidence < 0.4 should be HOLD.\n"
)


def _council_llm_prompt(signal: dict[str, Any]) -> str:
    """Tight signal-only prompt — no thesis, no catalysts, no
    macro context. Council is judging the immediate setup, not
    writing an investment memo. Keeps token count under ~150 in,
    ~80 out.
    """
    rsi = signal.get("rsi", "n/a")
    mom = signal.get("momentum_5b", "n/a")
    vol = signal.get("volume_ratio", "n/a")
    price = signal.get("price", signal.get("mid_price", "n/a"))
    return (
        f"Symbol setup:\n"
        f"  rsi={rsi}\n"
        f"  momentum_5b={mom}\n"
        f"  volume_ratio={vol}\n"
        f"  price={price}\n\n"
        f"Return your JSON decision now."
    )


async def _run_single_council_model(
    api_key: str, provider: str, model: str, prompt: str,
) -> dict[str, Any]:
    """One model in the panel. Defensive — never raises. JSON parse
    failure or any LLM error falls back to HOLD with confidence 0
    so the consensus call degrades gracefully under partial
    outages.

    Tracing: emits one Langfuse ``generation`` observation per
    call when tracing is enabled. Captures the prompt, raw
    completion, parsed action/confidence/reason, and the
    pre-computed cost. No-op when tracing is disabled — see
    :mod:`services.langfuse_tracer`.
    """
    import json as _json
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    from services.langfuse_tracer import atraced_span, span_update

    async with atraced_span(
        f"council_llm_{provider}",
        as_type="generation",
        input=prompt,
        model=f"{provider}/{model}",
        metadata={
            "provider": provider,
            "model": model,
            "engine": "council_llm",
        },
    ) as gen_span:
        try:
            chat = LlmChat(
                api_key=api_key,
                session_id=f"council_shadow_{provider}_{model}",
                system_message=_COUNCIL_SYSTEM_PROMPT,
            ).with_model(provider, model)
            response = await chat.send_message(UserMessage(text=prompt))
            raw = response if isinstance(response, str) else getattr(response, "text", str(response))
            raw_str = raw.strip()
            if "```json" in raw_str:
                raw_str = raw_str.split("```json", 1)[1].split("```", 1)[0].strip()
            elif raw_str.startswith("```"):
                raw_str = raw_str.split("```", 2)[1].split("```", 1)[0].strip()
            parsed = _json.loads(raw_str)
            action = canonicalise_action(parsed.get("action") or "HOLD")
            confidence = float(parsed.get("confidence") or 0.0)
            cost_usd = _LLM_COUNCIL_COST_USD.get(provider, 0.005)
            result = {
                "provider": provider,
                "action": action,
                "confidence": confidence,
                "reason": (parsed.get("reason") or "")[:100],
                "cost_usd": cost_usd,
                "ok": True,
            }
            # Stamp the generation with our pre-computed cost (the
            # SDK can't auto-cost EMERGENT_LLM_KEY pricing — we
            # know it locally via _LLM_COUNCIL_COST_USD).
            span_update(
                gen_span,
                output={
                    "action": action,
                    "confidence": confidence,
                    "reason": result["reason"],
                    "raw_completion": raw_str[:600],
                },
                cost_details={"total_cost": cost_usd},
                metadata={"ok": True},
            )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[council-llm] %s/%s failed, falling back to HOLD: %s",
                provider, model, exc,
            )
            span_update(
                gen_span,
                output={"action": "HOLD", "confidence": 0.0, "reason": f"{provider}_unavailable"},
                level="ERROR",
                status_message=f"{type(exc).__name__}: {str(exc)[:120]}",
                metadata={"ok": False},
            )
            return {
                "provider": provider,
                "action": "HOLD",
                "confidence": 0.0,
                "reason": f"{provider}_unavailable",
                "cost_usd": 0.0,
                "ok": False,
            }


def _council_llm_consensus(votes: list[dict[str, Any]]) -> dict[str, Any]:
    """Weighted consensus across the 3-model panel.

    Tie-breaking favours HOLD — we'd rather sit on the sidelines
    than fire a coin-flip dissent. Operators can observe the mixed
    votes in the per-model ``reason`` strings.
    """
    if not votes:
        return {"action": "HOLD", "confidence": 0.0, "thesis": "council_no_votes"}

    buckets: dict[str, dict[str, float]] = {
        "LONG": {"weight": 0.0, "confidence_sum": 0.0, "n": 0.0},
        "SHORT": {"weight": 0.0, "confidence_sum": 0.0, "n": 0.0},
        "HOLD": {"weight": 0.0, "confidence_sum": 0.0, "n": 0.0},
    }
    for v in votes:
        action = v.get("action") or "HOLD"
        if action not in buckets:
            action = "HOLD"
        w = _LLM_COUNCIL_WEIGHTS.get(v.get("provider"), 0.33)
        conf = float(v.get("confidence") or 0.0)
        scaled = w * max(conf, 0.1)
        buckets[action]["weight"] += scaled
        buckets[action]["confidence_sum"] += conf * w
        buckets[action]["n"] += w

    long_w = buckets["LONG"]["weight"]
    short_w = buckets["SHORT"]["weight"]
    hold_w = buckets["HOLD"]["weight"]
    if long_w > short_w and long_w > hold_w:
        winner = "LONG"
    elif short_w > long_w and short_w > hold_w:
        winner = "SHORT"
    else:
        winner = "HOLD"

    win_bucket = buckets[winner]
    win_conf = (
        win_bucket["confidence_sum"] / win_bucket["n"]
        if win_bucket["n"] > 0 else 0.0
    )
    panel_summary = " · ".join(
        f"{v.get('provider')}={v.get('action')}({v.get('confidence', 0):.2f})"
        for v in votes
    )
    return {
        "action": winner,
        "confidence": round(win_conf, 4),
        "thesis": f"council_llm_{winner.lower()} | {panel_summary}",
    }


async def _run_council_llm(signal: dict[str, Any]) -> dict[str, Any]:
    """Council v2 entrypoint. Fires 3 LLM calls in parallel via
    asyncio.gather, computes weighted consensus, returns canonical
    engine output dict with real ``llm_cost_usd``.

    Provider-model selection (Feb 2026):
        - openai/gpt-5.2
        - anthropic/claude-sonnet-4-5-20250929
        - gemini/gemini-2.5-flash (cheapest)

    Falls back to HOLD with zero confidence if EMERGENT_LLM_KEY is
    missing.

    Tracing: emits one Langfuse parent span per consensus round,
    with the three per-model generations nested as children. The
    parent span's output captures the weighted-vote winner and
    the panel summary so an operator can answer "why did Council
    HOLD on this signal?" by drilling into the trace.
    """
    import asyncio as _asyncio
    import os as _os
    from services.langfuse_tracer import atraced_span, span_update

    api_key = _os.environ.get("EMERGENT_LLM_KEY", "").strip()
    if not api_key:
        return {
            "action": "HOLD",
            "thesis": "council_llm_no_api_key",
            "confidence": 0.0,
            "llm_cost_usd": 0.0,
        }

    prompt = _council_llm_prompt(signal)
    panel = [
        ("openai", "gpt-5.2"),
        ("anthropic", "claude-sonnet-4-5-20250929"),
        ("gemini", "gemini-2.5-flash"),
    ]
    # Parent span — symbol + signal context so the operator can
    # filter the Langfuse UI by the trade that triggered the panel.
    async with atraced_span(
        "council_llm_panel",
        as_type="span",
        input={
            "symbol": str(signal.get("symbol") or ""),
            "asset_type": signal.get("asset_type"),
            "active_action": signal.get("action"),
            "active_confidence": signal.get("confidence"),
        },
        metadata={
            "panel": [f"{p}/{m}" for p, m in panel],
            "weights": _LLM_COUNCIL_WEIGHTS,
            "engine": "council_llm",
        },
    ) as panel_span:
        votes = await _asyncio.gather(
            *(_run_single_council_model(api_key, p, m, prompt) for p, m in panel),
            return_exceptions=False,
        )
        consensus = _council_llm_consensus(votes)
        total_cost = sum(float(v.get("cost_usd") or 0.0) for v in votes if v.get("ok"))
        span_update(
            panel_span,
            output={
                "action": consensus["action"],
                "confidence": consensus["confidence"],
                "thesis": consensus["thesis"],
                "votes": [
                    {
                        "provider": v.get("provider"),
                        "action": v.get("action"),
                        "confidence": v.get("confidence"),
                        "ok": v.get("ok"),
                    }
                    for v in votes
                ],
            },
            cost_details={"total_cost": total_cost},
            metadata={
                "votes_ok_count": sum(1 for v in votes if v.get("ok")),
                "votes_failed_count": sum(1 for v in votes if not v.get("ok")),
            },
        )
        return {
            "action": consensus["action"],
            "thesis": consensus["thesis"],
            "confidence": consensus["confidence"],
            "llm_cost_usd": round(total_cost, 6),
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

        # Disagreement-triggered cycle skip — saves LLM cost during
        # long agreement runs. Adversarial is exempt (free).
        # Entry/exit phases are exempt (highest-signal moments).
        if decision_phase == "cycle" and shadow_engine != ENGINE_ADVERSARIAL:
            agreement_run = await count_recent_agreement_run(db, bot_id)
            if should_skip_cycle_for_agreement_run(
                shadow_engine=shadow_engine,
                decision_phase=decision_phase,
                recent_agreement_run=agreement_run,
            ):
                logger.debug(
                    "[shadow] cycle-skip for agreement run: bot=%s run=%d",
                    bot_id, agreement_run,
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
        # Capture regime tag for regime-conditional analysis once
        # bucket counts are large enough to weight per-regime.
        regime = signal.get("regime") if isinstance(signal, dict) else None

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
            regime_at_decision=str(regime) if regime else None,
            trade_id=trade_id,
            active_trade_doc_id=active_trade_doc_id,
        )
        decision_id = await insert_shadow_decision(db, decision)
        await _mirror_to_stream_if_commander(decision_id, decision)
        return decision_id
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[shadow] fire_shadow swallowed exception bot=%s symbol=%s: %s",
            bot_id, symbol, exc,
        )
        return None


async def _mirror_to_stream_if_commander(
    decision_id: Optional[str],
    decision: "ShadowDecision",
) -> None:  # pragma: no cover — trivial glue
    """Mirror an adversarial (Commander-backed) decision to the
    per-market JSONL stream for grep-friendly observability.

    Intentionally scoped to ``ENGINE_ADVERSARIAL`` only — the
    file stream is labelled "Commander decisions" in the UI and
    the adversarial engine is the one that produces a true
    Bull/Bear/Commander verdict. Council shadows (rule-based v1)
    could mirror too if we relabel the feature, but for now
    keeping the scope narrow prevents muddling the two.
    """
    if decision_id is None:
        return
    if decision.shadow_engine != ENGINE_ADVERSARIAL:
        return
    try:
        from services.commander_decision_stream import append_commander_decision
        append_commander_decision(decision, decision.asset_type)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[shadow] commander stream mirror failed (non-critical): %s", exc,
        )
