"""Research Shadow — champion-challenger framework for trading engines.

What this module is
-------------------
A silent-half framework that lets a "shadow" engine ride alongside the
active engine on every bot cycle, recording what it *would* have done
without ever touching a real fill. The shadow's value is later scored
by a deferred worker (:mod:`services.research_shadow_scorer`) that
backfills tactical + strategic counterfactual P&L per dissent.

Why disagreement-conditional, not raw-agreement
-----------------------------------------------
A shadow engine that agrees with the active one 92% of the time looks
"good" but adds zero promotion evidence — it's just a follower. The
only number that earns Tier-3 promotion is **disagreement-conditional
accuracy**: when shadow dissents from active, who's right N trades
later? Everything in this framework is built around capturing those
dissent moments and forward-scoring them.

The Tier-3 firewall
-------------------
Shadow code MUST NOT write to:

* ``paper_trades`` / ``crypto_paper_trades`` (would inflate paper-day
  count → false Tier-3 unlock)
* ``prediction_tracker`` records (would skew calibration math)
* ``trading_bots[].stats.{winning_trades, total_trades, ...}`` (would
  poison bot-level performance metrics)
* The adversarial decision log's ``update_decision_outcome`` path
  (reserved for adversarial-active, not shadow)

Shadow writes ONLY to the new ``research_shadow_decisions`` collection.
Enforced by ``tests/test_shadow_tier3_isolation.py`` — that test must
stay green or this entire framework is invalid.

Asset-typed defaults
--------------------
Fill-cost and counterfactual-lookahead defaults differ by asset_type
from day 1. A flat 20bps round-trip makes equities look artificially
bad and options look artificially good — both will produce false
promotion/demotion signals. Defaults pinned in code, env-overridable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


# ── Asset-typed economics ─────────────────────────────────────────────────────


def _env_int(key: str, default: int) -> int:
    """Strict int env reader; falls back on parse failure."""
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# Round-trip simulated fill cost in basis points. Applied to shadow
# counterfactual P&L only — never to active fills (those have real
# slippage). Equities are tightest, options widest. Pinned defaults
# come from typical retail-broker market-impact studies; override per
# deployment via env.
FILL_COST_BPS: dict[str, int] = {
    "stock": _env_int("SHADOW_FILL_COST_BPS_STOCK", 8),
    "crypto": _env_int("SHADOW_FILL_COST_BPS_CRYPTO", 20),
    "options": _env_int("SHADOW_FILL_COST_BPS_OPTIONS", 100),
}

# Tactical lookahead window in seconds — the "did shadow look right
# 30 min later?" check. Options use a longer window because theta and
# spread snapshot timing dominate at the 30-min scale.
TACTICAL_LOOKAHEAD_S: dict[str, int] = {
    "stock": _env_int("SHADOW_TACTICAL_LOOKAHEAD_STOCK_S", 30 * 60),
    "crypto": _env_int("SHADOW_TACTICAL_LOOKAHEAD_CRYPTO_S", 30 * 60),
    "options": _env_int("SHADOW_TACTICAL_LOOKAHEAD_OPTIONS_S", 4 * 60 * 60),
}

# Maturity guardrail — disagreement-conditional metrics are NOT
# actionable until at least N dissents have been scored. Mirrors
# crypto_adversarial_stats.MIN_BUCKET_SAMPLES (15) and
# crypto_shadow_research_stats but with a higher floor: the shadow
# eval is the final gate before promoting an engine to active, so
# the bar is stricter.
MIN_DISSENT_SAMPLES = _env_int("SHADOW_MIN_DISSENT_SAMPLES", 30)


# ── Cost ceiling tiers (per-bot per-day) ──────────────────────────────────────


# Daily LLM-spend cap per bot for Council-style shadows (Adversarial
# shadow is deterministic and free). Three tiers: full operation up
# to 80% of cap, degraded (skip cycle shadows, keep entry/exit only)
# 80-100%, paused at 100%.
COST_CEILING_USD_PER_DAY: float = float(
    os.environ.get("SHADOW_COST_CEILING_USD_PER_DAY", "5.0"),
)
COST_DEGRADED_FRAC: float = 0.80
COST_PAUSED_FRAC: float = 1.00

# Minimum gap between LLM-shadow fires per bot — defence against a
# misconfigured 30-second cycle nuking the LLM bill.
LLM_SHADOW_MIN_GAP_S: int = _env_int("SHADOW_LLM_MIN_GAP_S", 60)


# ── Action canonicalisation ───────────────────────────────────────────────────


# Normalise the various ways engines spell their actions onto a
# 4-bucket canonical alphabet. Each engine is free to be expressive
# in its raw output; the dissent detector and scorer work in
# canonical space only.
#
# MUST include every verdict token the AI verdict pipeline emits — pre-
# 2026-05-01 STRONG_BUY / WEAK_BUY / STRONG_SELL / WEAK_SELL silently
# fell through the `.get(..., "HOLD")` default at the bottom of
# `canonicalise_action`, corrupting every shadow record from a
# STRONG_*-emitting engine into a phantom HOLD. Same bug class as
# `prediction_tracker.canonical_ai_dir` — see that helper for the
# centralized pattern.
_CANONICAL_ACTIONS: dict[str, str] = {
    "LONG": "LONG", "BUY": "LONG", "ENTRY_LONG": "LONG",
    "STRONG_BUY": "LONG", "WEAK_BUY": "LONG",
    "SHORT": "SHORT", "SELL": "SHORT", "ENTRY_SHORT": "SHORT",
    "STRONG_SELL": "SHORT", "WEAK_SELL": "SHORT",
    "SHORT_OR_AVOID": "SHORT",  # adversarial commander spelling
    "HOLD": "HOLD", "WAIT": "HOLD", "NO_TRADE": "HOLD", "NEUTRAL": "HOLD",
    "CLOSE": "CLOSE", "EXIT": "CLOSE", "FLAT": "CLOSE",
}

# Engine names — used as enum-ish strings on the wire. Keep them
# explicit constants so a typo in a route handler fails at import,
# not at runtime against a live shadow row.
ENGINE_NONE = "none"
ENGINE_CONFLUENCE = "confluence"
ENGINE_ADVERSARIAL = "adversarial"
ENGINE_COUNCIL = "council"

VALID_SHADOW_ENGINES = (ENGINE_ADVERSARIAL, ENGINE_COUNCIL)


def canonicalise_action(action: Optional[str]) -> str:
    """Map an engine's raw action string onto the 4-bucket canonical
    alphabet. Unknown values fall through to ``HOLD`` — the safe
    default that never shows as a dissent against an active HOLD."""
    if not action:
        return "HOLD"
    return _CANONICAL_ACTIONS.get(action.strip().upper(), "HOLD")


def detect_dissent(active_action: Optional[str], shadow_action: Optional[str]) -> bool:
    """True iff the two engines would fire materially different
    decisions on this cycle, after canonicalisation.

    Pure function — no I/O. Trivial to unit-test.
    """
    return canonicalise_action(active_action) != canonicalise_action(shadow_action)


# ── ShadowDecision schema ─────────────────────────────────────────────────────


@dataclass
class ShadowDecision:
    """Single shadow observation, ready for persistence.

    Fields map 1:1 onto Mongo document keys. Defaults reflect the
    "logged but not yet scored" state — the deferred worker patches
    ``tactical_score`` / ``strategic_score`` and flips
    ``pending_counterfactual`` to False.
    """
    bot_id: str
    user_id: str
    symbol: str
    asset_type: str
    decision_phase: str  # "entry" | "cycle" | "exit"
    active_engine: str
    active_action: str
    shadow_engine: str
    shadow_action: str
    mid_price: float
    sim_fill_bps_round_trip: int

    # Optional context — present when available, omitted when not.
    trade_id: Optional[str] = None
    active_trade_doc_id: Optional[str] = None
    shadow_thesis: Optional[str] = None
    shadow_confidence: Optional[float] = None
    llm_cost_usd: float = 0.0
    # Captured at decision-time so the deferred scorer can compute
    # volume-conditional fill costs without a second quote lookup.
    volume_ratio_at_decision: Optional[float] = None
    # Captured for regime-conditional analysis (P2 instrumentation).
    # Values: "trending", "parabolic", "uncertain", or asset-specific
    # tags. Future regime-conditional weighting will read this field.
    regime_at_decision: Optional[str] = None

    # Deferred-scoring fields. Worker sets these.
    tactical_score: Optional[dict[str, Any]] = None
    strategic_score: Optional[dict[str, Any]] = None
    pending_counterfactual: bool = True

    # System-managed fields.
    decision_id: str = field(default_factory=lambda: uuid4().hex)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_dissent: bool = field(init=False)

    def __post_init__(self) -> None:
        # Canonicalise on the way in so downstream code never has to
        # second-guess the input shape.
        self.active_action = canonicalise_action(self.active_action)
        self.shadow_action = canonicalise_action(self.shadow_action)
        self.is_dissent = self.active_action != self.shadow_action

    def to_doc(self) -> dict[str, Any]:
        """Mongo-ready dict. Excludes None-valued optional fields so
        the document stays compact and queries on optional keys
        behave sensibly with ``$exists``.

        Stamps ``tier3_firewall=True`` on every row as a defence-in-
        depth marker — anyone grepping the DB can confirm at a
        glance that this collection was designed Tier-3 safe. The
        marker is non-functional; the actual firewall is enforced
        by the test suite + the writer being the ONLY caller of
        ``research_shadow_decisions``.
        """
        doc: dict[str, Any] = {
            "decision_id": self.decision_id,
            "bot_id": self.bot_id,
            "user_id": self.user_id,
            "ts": self.ts,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "decision_phase": self.decision_phase,
            "active_engine": self.active_engine,
            "active_action": self.active_action,
            "shadow_engine": self.shadow_engine,
            "shadow_action": self.shadow_action,
            "is_dissent": self.is_dissent,
            "mid_price": self.mid_price,
            "sim_fill_bps_round_trip": self.sim_fill_bps_round_trip,
            "llm_cost_usd": self.llm_cost_usd,
            "pending_counterfactual": self.pending_counterfactual,
            "tier3_firewall": True,
        }
        if self.trade_id is not None:
            doc["trade_id"] = self.trade_id
        if self.active_trade_doc_id is not None:
            doc["active_trade_doc_id"] = self.active_trade_doc_id
        if self.shadow_thesis is not None:
            doc["shadow_thesis"] = self.shadow_thesis
        if self.shadow_confidence is not None:
            doc["shadow_confidence"] = self.shadow_confidence
        if self.volume_ratio_at_decision is not None:
            doc["volume_ratio_at_decision"] = self.volume_ratio_at_decision
        if self.regime_at_decision is not None:
            doc["regime_at_decision"] = self.regime_at_decision
        if self.tactical_score is not None:
            doc["tactical_score"] = self.tactical_score
        if self.strategic_score is not None:
            doc["strategic_score"] = self.strategic_score
        return doc


# ── Gate logic ────────────────────────────────────────────────────────────────


def should_fire_shadow(
    *,
    shadow_engine: str,
    last_shadow_ts: Optional[datetime],
    daily_cost_usd: float,
    decision_phase: str,
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    """Decide whether to fire a shadow this cycle.

    Returns ``(fire, reason)``. ``reason`` is a short tag operators
    can grep for in the logs to understand why a shadow was/wasn't
    fired.

    Rules (in order, fail-closed on first hit):

    1. Engine must be one of the valid shadow engines.
    2. If the engine is LLM-backed (Council), enforce per-bot
       rate-limit (``LLM_SHADOW_MIN_GAP_S``).
    3. If the engine is LLM-backed, enforce daily cost ceiling:

       * ``< 80%`` → fire
       * ``80-100%`` → degraded mode: skip ``cycle`` shadows,
         keep ``entry`` and ``exit`` (the highest-value moments)
       * ``>= 100%`` → pause entirely until tomorrow

    Adversarial shadow has no LLM cost so the cost gates don't
    apply — only the engine-validity check.
    """
    if shadow_engine not in VALID_SHADOW_ENGINES:
        return False, "invalid_engine"

    # Adversarial shadow: deterministic, free. Always fires.
    if shadow_engine == ENGINE_ADVERSARIAL:
        return True, "ok"

    # Council shadow gates from here down.
    _now = now or datetime.now(timezone.utc)

    if last_shadow_ts is not None:
        gap = (_now - last_shadow_ts).total_seconds()
        if gap < LLM_SHADOW_MIN_GAP_S:
            return False, "rate_limited"

    if daily_cost_usd >= COST_CEILING_USD_PER_DAY * COST_PAUSED_FRAC:
        return False, "cost_ceiling_paused"

    if daily_cost_usd >= COST_CEILING_USD_PER_DAY * COST_DEGRADED_FRAC:
        # Degraded mode — preserve the highest-signal moments
        # (entries + exits), skip mid-cycle.
        if decision_phase == "cycle":
            return False, "cost_ceiling_degraded"
        return True, "ok_degraded"

    return True, "ok"


# Disagreement-triggered cycle skip — when the last N consecutive
# cycles all AGREED with the active engine, skip the cycle shadow
# to save LLM cost. The signal value of cycle shadows is at
# inflection points (first dissent after a long agreement run);
# the 60th HOLD-vs-HOLD in a row is dead weight.
#
# Only applies to ``cycle`` phase shadows (entry + exit always
# fire — those are the highest-signal moments). Adversarial shadow
# is exempt because it's free.
CYCLE_SKIP_AGREEMENT_RUN: int = _env_int("SHADOW_CYCLE_SKIP_AGREEMENT_RUN", 8)


def should_skip_cycle_for_agreement_run(
    *,
    shadow_engine: str,
    decision_phase: str,
    recent_agreement_run: int,
) -> bool:
    """Decide whether to skip a cycle shadow because the recent
    agreement run is long enough that another cycle is unlikely to
    add signal.

    Pure function for testability. ``recent_agreement_run`` is the
    number of CONSECUTIVE most-recent shadow rows that were not
    dissents (i.e., active and shadow agreed). Reset to 0 the
    moment a dissent fires.

    Rules:
        - Adversarial shadow: never skipped (deterministic, free).
        - Entry / exit phases: never skipped (highest-signal moments).
        - Cycle phase + agreement run >= threshold: skip.
        - Otherwise: fire.
    """
    if shadow_engine == ENGINE_ADVERSARIAL:
        return False
    if decision_phase != "cycle":
        return False
    return recent_agreement_run >= CYCLE_SKIP_AGREEMENT_RUN


# ── PnL math (pure, used by scorer) ───────────────────────────────────────────


def volume_conditional_fill_bps(
    *,
    base_bps: int,
    volume_ratio: Optional[float],
    asset_type: str,
) -> int:
    """Scale the round-trip fill cost based on the volume regime at
    the time of the decision.

    Why this matters
    ----------------
    A flat 20bps round-trip across all crypto trades is a lie that
    flatters quiet-tape entries and punishes high-vol entries. Real
    execution sees the opposite shape: thinly traded conditions
    eat 30-50bps in spread, busy tape compresses to ~10bps. The
    Tier-3 promotion math is sensitive to this — a Council that
    only fires on quiet days will look better than it really is
    under flat-fee accounting.

    Conservative scaling envelope (clamped both ends so the math
    can't spike absurdly):

    * ``volume_ratio < 0.5``  → 1.5× base (low-vol penalty)
    * ``volume_ratio 0.5-1.5``→ 1.0× base (normal regime)
    * ``volume_ratio 1.5-3``  → 0.75× base (busy-tape benefit)
    * ``volume_ratio >= 3``   → 0.5× base (high-conviction tape)

    Equity stocks are tighter than crypto — same shape but 75% of
    the swing. Options keep the flat default (spread is structural,
    not volume-driven at retail scale).

    Returns the asset-typed default unchanged when ``volume_ratio``
    is missing or non-numeric (the fallback case is the v1 shape,
    so behaviour matches pre-volume-conditional builds).
    """
    if volume_ratio is None:
        return base_bps
    try:
        vr = float(volume_ratio)
    except (TypeError, ValueError):
        return base_bps
    if vr <= 0:
        return base_bps

    # Options: spread dominates, volume signal is noisy at retail.
    # Keep flat.
    if asset_type == "options":
        return base_bps

    # Pick raw multiplier from the envelope.
    if vr < 0.5:
        mult = 1.5
    elif vr < 1.5:
        mult = 1.0
    elif vr < 3.0:
        mult = 0.75
    else:
        mult = 0.5

    # Equity attenuation — tighter spread regime, smaller swings.
    if asset_type == "stock" and mult != 1.0:
        # Pull the multiplier 25% closer to 1.0
        mult = 1.0 + (mult - 1.0) * 0.75

    return max(1, int(round(base_bps * mult)))


def hypothetical_pnl_usd(
    *,
    action: str,
    entry_price: float,
    exit_price: float,
    notional_usd: float,
    fill_cost_bps: int,
) -> float:
    """Compute the hypothetical $ P&L of a simulated trade after
    round-trip fill cost.

    LONG profits when ``exit_price > entry_price``. SHORT profits
    when ``exit_price < entry_price``. HOLD and CLOSE have zero P&L
    by definition (no position taken).

    ``fill_cost_bps`` is round-trip — we apply it ONCE here, not
    twice. Pulled out as a pure function so the scorer's "did
    shadow look right" verdict is identical to what the operator
    sees in the drawer.
    """
    canonical = canonicalise_action(action)
    if canonical not in ("LONG", "SHORT"):
        return 0.0
    if entry_price <= 0 or notional_usd <= 0:
        return 0.0
    direction = 1.0 if canonical == "LONG" else -1.0
    raw_return = direction * (exit_price - entry_price) / entry_price
    cost = fill_cost_bps / 10_000.0
    return float(notional_usd * (raw_return - cost))
