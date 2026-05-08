"""RISEDUAL Terminal Aggregator.

Composes existing system state into terminal-ready responses.

Non-negotiable rules (per user spec):
    * No fresh API calls.
    * No trading decisions created here.
    * No model inference here.
    * Pure view aggregation over existing collections/services.

Answers five product questions:
    1. Is the market safe?
    2. What is this symbol doing?
    3. Why?
    4. What can go wrong?
    5. How much risk is allowed?

Motor-async throughout (this codebase uses Motor for the DB layer).
Every public function is awaitable — tests inject a ``TerminalContext``
with a fake Motor-style collection surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal


ConvictionTier = Literal["WEAK", "MODERATE", "STRONG", "VERY_STRONG"]
MarketState = Literal["CLOSED", "ACTIVE", "STRESSED", "UNSTABLE"]
TradeabilityState = Literal["NOT_TRADEABLE", "STABILIZING", "TRADEABLE"]
SessionPhase = Literal[
    "PRE_MARKET", "OPENING", "MIDDAY", "CLOSING", "AFTER_HOURS", "CLOSED", "ACTIVE"
]

SNAPSHOT_MAX_AGE_SECONDS = 600
LIQUIDITY_STRESS_CAUTION = 4.0
LIQUIDITY_STRESS_UNSTABLE = 6.0


# ═══════════════ context ═══════════════


@dataclass(frozen=True)
class TerminalContext:
    """Tests inject a fake ``db`` that mimics Motor's async collection API.
    Prod uses the real ``motor.motor_asyncio.AsyncIOMotorDatabase`` via
    ``routes/terminal.py``. ``now`` is an override hook — production leaves
    it None so wall-clock is used; tests pass a frozen UTC datetime.
    """
    db: Any
    now: datetime | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now(ctx: TerminalContext) -> datetime:
    return ctx.now or _utcnow()


# ═══════════════ parsing helpers ═══════════════


def _parse_dt(value: Any) -> datetime | None:
    """Coerce mixed datetime representations to aware UTC. Accepts
    ``datetime`` (naive or aware) and ISO-8601 strings with either ``Z``
    or ``+00:00`` offsets. Returns None on any parse failure — callers
    treat None as "unknown age = stale" (the conservative default)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _is_fresh(
    doc: dict[str, Any] | None,
    now: datetime,
    max_age_seconds: int = SNAPSHOT_MAX_AGE_SECONDS,
) -> bool:
    if not doc:
        return False
    updated_at = _parse_dt(doc.get("updated_at"))
    if not updated_at:
        return False
    age = (now - updated_at).total_seconds()
    return 0 <= age <= max_age_seconds


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# ═══════════════ stress / maturity math ═══════════════


def _liquidity_stress(avg_spread_bps: Any, p90_spread_bps: Any) -> float | None:
    avg = _safe_float(avg_spread_bps, 0.0)
    p90 = _safe_float(p90_spread_bps, 0.0)
    if avg <= 0 or p90 <= 0:
        return None
    return round(p90 / avg, 3)


def _stress_state(
    score: float | None,
) -> Literal["UNKNOWN", "NORMAL", "CAUTION", "STRESSED", "UNSTABLE"]:
    if score is None:
        return "UNKNOWN"
    if score >= LIQUIDITY_STRESS_UNSTABLE:
        return "UNSTABLE"
    if score >= LIQUIDITY_STRESS_CAUTION:
        return "STRESSED"
    if score >= 2.5:
        return "CAUTION"
    return "NORMAL"


def _session_phase(now: datetime, is_market_open: bool) -> SessionPhase:
    """UTC windows for a US regular session:
        13:30–14:00 → OPENING
        14:00–19:30 → MIDDAY
        19:30–21:00 → CLOSING
    The cron options warmer already no-ops outside these windows, so if
    markets are closed we return CLOSED regardless of clock time.
    """
    if not is_market_open:
        return "CLOSED"
    mins = now.hour * 60 + now.minute
    if 810 <= mins < 840:
        return "OPENING"
    if 840 <= mins < 1170:
        return "MIDDAY"
    if 1170 <= mins <= 1260:
        return "CLOSING"
    return "ACTIVE"  # defensive fallback


# ═══════════════ market-state ═══════════════


def _find_symbol_snapshot(
    option_snapshot: dict[str, Any] | None, symbol: str,
) -> dict[str, Any] | None:
    if not option_snapshot:
        return None
    for row in option_snapshot.get("data", []) or []:
        if row.get("symbol") == symbol:
            return row
    return None


def _aggregate_market_liquidity(
    option_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Walk every per-symbol row in the snapshot and compute the
    terminal-level summary stats. Designed to be forgiving of partial
    rows — one missing aggregate doesn't zero out the whole view."""
    rows = (option_snapshot or {}).get("data", []) or []
    tradeable = 0
    tracked = len(rows)
    stress_scores: list[float] = []
    p90_values: list[float] = []

    for row in rows:
        agg = row.get("aggregate", {}) or {}
        maturity = row.get("flow_maturity")
        if maturity is None:
            maturity = agg.get("flow_maturity")
        has_hot_flow = bool(row.get("has_hot_flow") or agg.get("has_hot_flow"))
        avg = agg.get("avg_spread_bps")
        p90 = agg.get("p90_spread_bps")
        score = _liquidity_stress(avg, p90)

        if score is not None:
            stress_scores.append(score)
        p90_float = _safe_float(p90, 0.0)
        if p90_float > 0:
            p90_values.append(p90_float)

        if maturity is True and has_hot_flow and _stress_state(score) not in {"STRESSED", "UNSTABLE"}:
            tradeable += 1

    avg_stress = round(sum(stress_scores) / len(stress_scores), 3) if stress_scores else None
    avg_p90 = round(sum(p90_values) / len(p90_values), 2) if p90_values else None

    return {
        "tracked_symbols": tracked,
        "tradeable_symbols": tradeable,
        "avg_stress_score": avg_stress,
        "avg_p90_spread_bps": avg_p90,
        "stress_state": _stress_state(avg_stress),
    }


async def get_market_state(ctx: TerminalContext) -> dict[str, Any]:
    """Answers Q1: is the market safe to trade?

    Single-doc read over ``option_universe._id="current"``. Stale or
    missing snapshot → CLOSED state with all counts zeroed. No fresh
    API calls, no model invocations."""
    now = _now(ctx)
    option_snapshot = await ctx.db["option_universe"].find_one({"_id": "current"})
    fresh = _is_fresh(option_snapshot, now)

    if not fresh:
        return {
            "market_state": "CLOSED",
            "session_phase": "CLOSED",
            "is_market_open": False,
            "snapshot_fresh": False,
            "tradeable_symbols": 0,
            "tracked_symbols": 0,
            "liquidity_stress": {
                "state": "UNKNOWN",
                "score": None,
                "avg_p90_spread_bps": None,
            },
            "updated_at": now.isoformat(),
        }

    is_market_open = bool(option_snapshot.get("is_market_open", False))
    liq = _aggregate_market_liquidity(option_snapshot)
    phase = _session_phase(now, is_market_open)

    if not is_market_open:
        market_state: MarketState = "CLOSED"
    elif liq["stress_state"] == "UNSTABLE":
        market_state = "UNSTABLE"
    elif liq["stress_state"] == "STRESSED":
        market_state = "STRESSED"
    else:
        market_state = "ACTIVE"

    return {
        "market_state": market_state,
        "session_phase": phase,
        "is_market_open": is_market_open,
        "snapshot_fresh": True,
        "tradeable_symbols": liq["tradeable_symbols"],
        "tracked_symbols": liq["tracked_symbols"],
        "liquidity_stress": {
            "state": liq["stress_state"],
            "score": liq["avg_stress_score"],
            "avg_p90_spread_bps": liq["avg_p90_spread_bps"],
        },
        "updated_at": option_snapshot.get("updated_at") or now.isoformat(),
    }


# ═══════════════ signal detail ═══════════════


def _conviction_label(score: Any) -> ConvictionTier:
    val = _safe_float(score, 0.0)
    if val >= 0.85:
        return "VERY_STRONG"
    if val >= 0.70:
        return "STRONG"
    if val >= 0.55:
        return "MODERATE"
    return "WEAK"


def _tradeability(row: dict[str, Any] | None) -> dict[str, Any]:
    """Consolidated per-symbol tradeability verdict the UI renders as
    a single badge. Uses fields the warm writer stamps on every row:
    ``flow_maturity``, ``has_hot_flow``, ``stable_minutes``, and the
    aggregate spread distribution."""
    if not row:
        return {
            "state": "NOT_TRADEABLE",
            "maturity": "UNKNOWN",
            "stable_minutes": 0,
            "has_hot_flow": False,
            "liquidity_stress_state": "UNKNOWN",
            "liquidity_stress_score": None,
            "reason": "NO_FRESH_OPTIONS_SNAPSHOT",
        }

    agg = row.get("aggregate", {}) or {}
    has_hot_flow = bool(row.get("has_hot_flow") or agg.get("has_hot_flow"))
    stable_minutes = int(_safe_float(
        row.get("stable_minutes") if row.get("stable_minutes") is not None
        else agg.get("stable_minutes"),
        0,
    ))
    flow_maturity = bool(row.get("flow_maturity") or agg.get("flow_maturity"))

    stress_score = _liquidity_stress(agg.get("avg_spread_bps"), agg.get("p90_spread_bps"))
    stress_state = _stress_state(stress_score)

    if stress_state in {"STRESSED", "UNSTABLE"}:
        state: TradeabilityState = "NOT_TRADEABLE"
        maturity = "STRESSED"
    elif flow_maturity and has_hot_flow:
        state = "TRADEABLE"
        maturity = "MATURE"
    elif stable_minutes > 0:
        state = "STABILIZING"
        maturity = "STABILIZING"
    else:
        state = "NOT_TRADEABLE"
        maturity = "IMMATURE"

    return {
        "state": state,
        "maturity": maturity,
        "stable_minutes": stable_minutes,
        "has_hot_flow": has_hot_flow,
        "liquidity_stress_state": stress_state,
        "liquidity_stress_score": stress_score,
    }


# Labels for the conviction-breakdown factors — must stay aligned with
# the keys ``services.conviction_service.compute_conviction`` emits. If
# that service adds a new breakdown key, add its label here so the UI
# renders a human-readable row instead of the raw field name.
_WHY_LABELS = {
    "signal_confidence": "Core signal confidence",
    "regime_match": "Regime alignment",
    "calibration": "Calibration quality",
    "options_flow_boost": "Options flow support",
    "liquidity_maturity": "Liquidity maturity",
    "rejection_bias_penalty": "Rejection-history penalty",
    "loss_streak_penalty": "Recent loss-streak penalty",
    "risk_reward": "Risk/reward quality",
}


def _build_why_from_breakdown(
    breakdown: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Only real conviction-breakdown contributions surface here. Zero-
    weight factors are dropped so the UI doesn't show a noise row for
    every disabled feature. Sorted by absolute magnitude — the biggest
    mover is always first whether it's a positive or negative factor."""
    if not breakdown:
        return []
    out: list[dict[str, Any]] = []
    for key, raw in breakdown.items():
        contribution = _safe_float(raw, 0.0)
        if contribution == 0:
            continue
        out.append({
            "factor": key,
            "label": _WHY_LABELS.get(key, key.replace("_", " ").title()),
            "impact": "positive" if contribution > 0 else "negative",
            "contribution": round(contribution, 4),
        })
    return sorted(out, key=lambda x: abs(x["contribution"]), reverse=True)


def _build_risks(
    signal_doc: dict[str, Any] | None,
    option_row: dict[str, Any] | None,
    position_context: dict[str, Any] | None,
    catalyst_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Risks come from four sources that each own one axis of ground
    truth: the signal itself (volatility / calibration risk flags), the
    options aggregate (liquidity regime), position context
    (correlation / double-exposure), and the catalyst snapshot
    (news shock / event risk). We never mutate — always rebuild."""
    risks: list[dict[str, Any]] = []
    signal_doc = signal_doc or {}
    position_context = position_context or {}

    for flag in signal_doc.get("risk_flags", []) or []:
        risks.append({
            "label": str(flag),
            "severity": "medium",
            "source": "signal",
        })

    if option_row:
        agg = option_row.get("aggregate", {}) or {}
        stress_score = _liquidity_stress(agg.get("avg_spread_bps"), agg.get("p90_spread_bps"))
        stress_state = _stress_state(stress_score)
        if stress_state in {"STRESSED", "UNSTABLE"}:
            risks.append({
                "label": "Liquidity stress elevated",
                "severity": "high" if stress_state == "UNSTABLE" else "medium",
                "source": "options_liquidity",
            })
        mean_iv = agg.get("mean_iv")
        if mean_iv is not None and _safe_float(mean_iv) > 0.65:
            risks.append({
                "label": "IV elevated",
                "severity": "medium",
                "source": "options",
            })

    if position_context.get("correlation_flag") == "HIGH":
        risks.append({
            "label": "High correlated exposure",
            "severity": "high",
            "source": "portfolio",
        })
    if position_context.get("already_owned") is True:
        risks.append({
            "label": "Existing position already open",
            "severity": "medium",
            "source": "portfolio",
        })

    # Phase C — catalyst / news shock risks. High shock = "restricted"
    # event risk surfaces as a high-severity chip; elevated = medium;
    # directional sentiment always surfaces at low severity so the
    # operator sees it even on quiet news days.
    if catalyst_snapshot:
        event_risk = catalyst_snapshot.get("event_risk")
        shock = catalyst_snapshot.get("news_shock", {}) or {}
        if event_risk == "restricted":
            risks.append({
                "label": "News shock restricted",
                "severity": "high",
                "source": "catalyst",
            })
        elif event_risk == "elevated":
            risks.append({
                "label": "Catalyst risk elevated",
                "severity": "medium",
                "source": "catalyst",
            })
        sentiment_label = shock.get("sentiment_label")
        if sentiment_label in {"bullish", "bearish"}:
            risks.append({
                "label": f"News sentiment: {sentiment_label}",
                "severity": "low",
                "source": "catalyst",
            })

    return risks


async def get_signal(
    ctx: TerminalContext, symbol: str, user_id: str | None = None,
) -> dict[str, Any]:
    """Answers Q2-Q5 for one symbol. Read-only: never creates a new
    decision. Reads latest persisted signal snapshot + current options
    snapshot + optional per-user position context.

    Collections that don't yet exist (``latest_signal_snapshots``,
    ``conviction_calibration``, ``position_context``) return None and
    degrade gracefully — the terminal will show a symbol page with
    tradeability + liquidity, just without the signal overlay. That
    keeps this endpoint useful in the pre-seeding window without
    failing hard."""
    now = _now(ctx)
    symbol = symbol.upper().strip()

    option_snapshot = await ctx.db["option_universe"].find_one({"_id": "current"})
    fresh_options = option_snapshot if _is_fresh(option_snapshot, now) else None
    option_row = _find_symbol_snapshot(fresh_options, symbol)

    signal_doc = await ctx.db["latest_signal_snapshots"].find_one({"symbol": symbol}) or {}
    conviction_score = signal_doc.get("conviction_score")
    conviction_tier = signal_doc.get("conviction_tier") or _conviction_label(conviction_score)

    calibration = await ctx.db["conviction_calibration"].find_one(
        {"tier": conviction_tier},
    ) or {}

    position_context: dict[str, Any] = {}
    if user_id:
        position_context = await ctx.db["position_context"].find_one(
            {"user_id": user_id, "symbol": symbol},
        ) or {}

    tradeability = _tradeability(option_row)
    agg = option_row.get("aggregate", {}) if option_row else {}

    # Phase C — catalyst snapshot (NEWS_SHOCK statistical layer).
    # Read-only: the feeders scheduler is the sole writer. Missing
    # snapshot → no catalyst block; UI gracefully hides the chip.
    catalyst_snapshot = await ctx.db["catalyst_snapshots"].find_one(
        {"symbol": symbol}, {"_id": 0},
    ) or {}

    size = signal_doc.get("suggested_size") or {}
    # Sizing always carries its explainer — the "anchored sizing" rule
    # from the product spec. Never surface a raw percentage without the
    # basis it's applied against.
    sizing = {
        "label": size.get("label", "N/A"),
        "risk_budget_pct": size.get("risk_budget_pct"),
        "estimated_dollars": size.get("estimated_dollars"),
        "basis": "risk_budget",
        "explainer": (
            "Sizing is a percent of allowed risk budget, "
            "not percent of total account."
        ),
    }

    return {
        "symbol": symbol,
        "action": signal_doc.get("action", "UNKNOWN"),
        "time_horizon": signal_doc.get("time_horizon", "UNKNOWN"),
        "conviction": {
            "tier": conviction_tier,
            "score": conviction_score,
            "calibration_context": {
                "sample_size": calibration.get("sample_size"),
                "hit_rate": calibration.get("hit_rate"),
                "window_days": calibration.get("window_days"),
            },
        },
        "sizing": sizing,
        "tradeability": tradeability,
        "why": _build_why_from_breakdown(signal_doc.get("conviction_breakdown")),
        "risks": _build_risks(signal_doc, option_row, position_context, catalyst_snapshot),
        "liquidity": {
            "avg_spread_bps": agg.get("avg_spread_bps"),
            "p90_spread_bps": agg.get("p90_spread_bps"),
            "stress_score": tradeability.get("liquidity_stress_score"),
            "flow_imbalance": agg.get("flow_imbalance"),
        },
        "options": {
            "snapshot_fresh": fresh_options is not None,
            "has_hot_flow": bool(option_row.get("has_hot_flow")) if option_row else False,
            "put_call_ratio": agg.get("put_call_ratio"),
            "total_volume": agg.get("total_volume"),
            "mean_iv": agg.get("mean_iv"),
            "top_contracts": option_row.get("contracts", []) if option_row else [],
        },
        "catalyst": {
            "event_risk": catalyst_snapshot.get("event_risk", "unknown"),
            "news_shock": catalyst_snapshot.get("news_shock"),
            "headline_chip": (
                (catalyst_snapshot.get("news_shock") or {}).get("latest_headline")
                if catalyst_snapshot else None
            ),
        } if catalyst_snapshot else None,
        "position_context": position_context or None,
        "updated_at": now.isoformat(),
    }


# ═══════════════ top actions (T2) ═══════════════
#
# Phase T2: prioritized action queue for the operator's terminal.
# Composes existing collections — sovereign_decisions (latest signals),
# paper_trades (open positions), option_universe (tradeability) — into
# ranked action cards.
#
# Default correlation rules (operator skipped explicit rules):
#   * Hard cap of 5 ENTER actions per response (concentration guard).
#   * Each open paper_trade in the same symbol replaces a fresh ENTER
#     with a MANAGE card so we never recommend doubling down silently.
#   * No sector-correlation logic in T2 — re-open if the operator
#     wants tighter coupling (would need symbol→sector resolver +
#     beta data, both already in the codebase but out of scope for
#     T2's "default" path).

TOP_ACTIONS_LOOKBACK_HOURS = 24
TOP_ACTIONS_MAX_DEFAULT = 10
TOP_ACTIONS_ENTER_CAP = 5

# Conviction-tier → priority anchor. Confidence within a tier nudges
# rank ordering. Keep this tight: we don't want a 0.71-confidence
# "medium" sneaking ahead of a 0.85-confidence "high".
_TIER_PRIORITY_ANCHOR = {
    "high": 0.80,
    "VERY_STRONG": 0.85,
    "STRONG": 0.75,
    "medium": 0.55,
    "MODERATE": 0.55,
    "low": 0.30,
    "WEAK": 0.30,
}

ActionKind = Literal["MANAGE_POSITION", "ENTER", "WATCH", "EXIT"]


def _action_priority_score(
    *,
    confidence: float,
    tier: str,
    size_multiplier: float,
    decision_age_minutes: float,
) -> float:
    """Pure ranking score in ``[0, 1]``.

    Composition:
    * ~70% conviction (anchored on the tier and adjusted by raw
      confidence within the tier).
    * ~20% size multiplier — Sovereign already encodes "how aggressive
      should we be" in the size multiplier. Tradeability + risk vetoes
      drag this down upstream.
    * ~10% freshness decay — older decisions lose priority linearly
      down to 0 at the lookback horizon.
    """
    anchor = _TIER_PRIORITY_ANCHOR.get(tier, 0.50)
    confidence = max(0.0, min(1.0, float(confidence or 0.0)))
    size = max(0.0, min(1.5, float(size_multiplier or 0.0)))
    # Linear freshness decay from 1.0 at age=0 to 0.0 at the horizon.
    horizon_minutes = TOP_ACTIONS_LOOKBACK_HOURS * 60
    freshness = max(0.0, 1.0 - (decision_age_minutes / horizon_minutes))

    raw = (
        0.70 * (0.5 * anchor + 0.5 * confidence)
        + 0.20 * (size / 1.5)
        + 0.10 * freshness
    )
    return round(max(0.0, min(1.0, raw)), 4)


def _classify_kind(
    *,
    action: str,
    tier: str,
    confidence: float,
    has_open_position: bool,
    position_direction: str | None = None,
) -> ActionKind:
    """Map a sovereign decision + position context into one of the
    four action kinds the terminal renders.

    Routing rules (applied in order):

    * **EXIT** — fired when the user has an open position AND the
      latest sovereign decision *reverses* the side at high conviction.
      This is the only way EXIT cards get emitted in v1; SL/TP breach
      detection needs a live-price hop and is handled by a future
      pass when position-storage collections are reliably populated.
    * **MANAGE_POSITION** — open position, sovereign agrees with the
      side or is HOLD/uncertain. Operator owns the close decision.
    * **ENTER** — no open position + high-conviction directional
      signal (LONG/SHORT). The "act now" prompt.
    * **WATCH** — every other signal worth surfacing.
    """
    HIGH_CONVICTION_TIERS = ("high", "STRONG", "VERY_STRONG")
    high_conviction = tier in HIGH_CONVICTION_TIERS or confidence >= 0.70

    if has_open_position and position_direction:
        from services.prediction_tracker import canonical_ai_dir
        pos_dir = canonical_ai_dir(position_direction)
        signal_dir = canonical_ai_dir(action)
        # Reversal trigger — only when the model speaks with conviction.
        # A low-conviction reversal is noise and shouldn't pull the
        # operator out of a thesis.
        is_reversal = (
            pos_dir in {"LONG", "SHORT"}
            and signal_dir in {"LONG", "SHORT"}
            and pos_dir != signal_dir
            and high_conviction
        )
        if is_reversal:
            return "EXIT"
        return "MANAGE_POSITION"

    if has_open_position:
        return "MANAGE_POSITION"

    from services.prediction_tracker import canonical_ai_dir
    if canonical_ai_dir(action) in {"LONG", "SHORT"}:
        if high_conviction:
            return "ENTER"
        return "WATCH"
    return "WATCH"


def _reason_text(
    *,
    kind: ActionKind,
    action: str,
    tier: str,
    confidence: float,
    has_open_position: bool,
    vetoes_count: int,
    position_direction: str | None = None,
) -> str:
    parts: list[str] = []
    if kind == "EXIT":
        pos = (position_direction or "?").upper()
        parts.append(
            f"Sovereign reversed to {action} on open {pos} position"
        )
    elif kind == "MANAGE_POSITION":
        parts.append(f"Open position; latest sovereign view is {action}")
    elif kind == "ENTER":
        parts.append(f"Sovereign {action}")
    elif kind == "WATCH":
        parts.append(f"Sovereign {action} below entry threshold")
    parts.append(f"{tier} tier")
    parts.append(f"conf {confidence:.2f}")
    if vetoes_count:
        parts.append(f"{vetoes_count} active veto(es)")
    if not has_open_position and kind == "ENTER":
        parts.append("no open position")
    return " · ".join(parts)


async def _latest_decisions_per_symbol(
    ctx: TerminalContext, *, since: datetime, max_universe: int = 200,
) -> list[dict[str, Any]]:
    """Pull the freshest sovereign decision for each symbol seen in
    the lookback window. Single ``find().sort().to_list`` then
    Python-side dedupe — keeps test stubs simple and the I/O at one
    Mongo round trip."""
    cursor = ctx.db["sovereign_decisions"].find(
        {"created_at": {"$gte": since}},
    ).sort("created_at", -1).limit(max_universe * 5)
    rows = await cursor.to_list(max_universe * 5)
    seen: set[str] = set()
    latest: list[dict[str, Any]] = []
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        latest.append(row)
        if len(latest) >= max_universe:
            break
    return latest


async def _open_positions_for_user(
    ctx: TerminalContext, user_id: str | None,
) -> dict[str, dict[str, Any]]:
    """Map ``symbol -> open paper_trade row`` for the requesting user.

    No user_id → empty map (the terminal still surfaces ENTER actions
    on a global view). Crypto + equity open trades are merged so the
    operator sees one unified action queue.
    """
    if not user_id:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for coll, sym_field in (("paper_trades", "ticker"), ("crypto_paper_trades", "symbol")):
        cursor = ctx.db[coll].find(
            {"status": "open", "user_id": user_id},
        )
        rows = await cursor.to_list(length=200)
        for row in rows:
            sym = (row.get(sym_field) or row.get("symbol") or "").upper()
            if sym:
                out[sym] = row
    # For equity positions, merge in stop_loss / take_profit from the
    # portfolios collection — the paper_trades row is a trade record
    # and doesn't carry the live SL/TP. Crypto rows already have the
    # fields inline so no extra lookup needed there.
    try:
        portfolio = await ctx.db["portfolios"].find_one(
            {"user_id": user_id}, {"_id": 0},
        )
    except Exception:  # noqa: BLE001
        portfolio = None
    if portfolio:
        for pos in (portfolio.get("positions") or []):
            sym = (pos.get("symbol") or "").upper()
            if sym and sym in out:
                # Don't clobber fields already on the trade row; only
                # fill SL/TP/avg_cost when missing.
                for k in ("stop_loss", "take_profit", "avg_cost"):
                    if out[sym].get(k) is None and pos.get(k) is not None:
                        out[sym][k] = pos[k]
    return out


# ── Hard exit triggers (SL / TP breach detection) ─────────────────
#
# Phase 2 of the EXIT rule layer. Where v1 EXIT cards fired on
# sovereign-side reversal, v2 adds *price-driven* EXIT cards:
# when the live mid-price breaches an open position's stop_loss or
# take_profit, that symbol gets a top-ranked EXIT with an explicit
# "SL breach" / "TP hit" reason.
#
# Gated on position_direction being set AND the trade row carrying
# a numeric stop_loss/take_profit. Any failure of the live-price
# hop degrades silently — a stale price source is *never* allowed to
# fabricate an EXIT card.


def _hard_exit_trigger(
    *,
    direction: str,
    price: float,
    stop_loss: float | None,
    take_profit: float | None,
) -> tuple[str, str] | None:
    """Pure-function SL/TP breach classifier.

    Returns ``(trigger, reason_snippet)`` or ``None`` when no breach.

    LONG rules:
      * price ≤ stop_loss → ("sl_breach", "Stop-loss breached @ <price>")
      * price ≥ take_profit → ("tp_hit", "Take-profit hit @ <price>")

    SHORT rules (symmetric):
      * price ≥ stop_loss → ("sl_breach", ...)
      * price ≤ take_profit → ("tp_hit", ...)
    """
    if price <= 0:
        return None
    d = (direction or "").upper()
    sl = float(stop_loss) if stop_loss is not None else None
    tp = float(take_profit) if take_profit is not None else None

    if d == "LONG":
        if sl is not None and sl > 0 and price <= sl:
            return ("sl_breach", f"Stop-loss breached @ ${price:.2f}")
        if tp is not None and tp > 0 and price >= tp:
            return ("tp_hit", f"Take-profit hit @ ${price:.2f}")
    elif d == "SHORT":
        if sl is not None and sl > 0 and price >= sl:
            return ("sl_breach", f"Stop-loss breached @ ${price:.2f}")
        if tp is not None and tp > 0 and price <= tp:
            return ("tp_hit", f"Take-profit hit @ ${price:.2f}")
    return None


async def _build_hard_exit_cards(
    ctx: TerminalContext,
    open_positions: dict[str, dict[str, Any]],
    *,
    price_fetcher=None,
) -> list[dict[str, Any]]:
    """Probe open positions for SL/TP breaches and emit a fully-
    formed action card for each breach. Returns ``[]`` on any failure
    so a broken live-price provider can never suppress the rest of
    the top-actions response.

    ``price_fetcher`` is injectable for tests. Defaults to
    ``services.price_provider.get_quote`` which the sovereign path
    already uses.
    """
    if not open_positions:
        return []
    if price_fetcher is None:
        try:
            from services.price_provider import get_quote as price_fetcher  # type: ignore
        except Exception:  # noqa: BLE001
            return []

    out: list[dict[str, Any]] = []
    for symbol, pos in open_positions.items():
        from services.prediction_tracker import canonical_ai_dir
        direction = canonical_ai_dir(
            pos.get("direction") or pos.get("side") or "",
        )
        # canonical_ai_dir returns "LONG" / "SHORT" / "UNKNOWN" — anything
        # else (HOLD, malformed, missing) becomes "UNKNOWN" and is skipped.
        if direction == "UNKNOWN":
            continue
        sl = pos.get("stop_loss")
        tp = pos.get("take_profit")
        if sl is None and tp is None:
            continue
        try:
            quote = await price_fetcher(symbol)
        except Exception:  # noqa: BLE001
            quote = None
        price = float((quote or {}).get("price") or 0.0)
        trigger = _hard_exit_trigger(
            direction=direction, price=price,
            stop_loss=sl, take_profit=tp,
        )
        if trigger is None:
            continue
        trigger_kind, reason_snippet = trigger
        out.append({
            "kind": "EXIT",
            "symbol": symbol,
            "asset_type": pos.get("asset_class") or "equity",
            "action": "CLOSE",
            "priority_score": 0.99,  # Hard breaches outrank reversals (0.95).
            "conviction": {"tier": "hard", "score": None},
            "size_multiplier": 0.0,
            "vetoes": [],
            "vetoes_count": 0,
            "reason": (
                f"{reason_snippet} · open {direction} position "
                f"(entry/avg: {pos.get('entry_price') or pos.get('avg_cost') or '—'}, "
                f"SL: {sl}, TP: {tp})"
            ),
            "correlation_note": None,
            "decision_id": None,
            "decision_age_minutes": 0.0,
            "shadow": True,
            "trigger": trigger_kind,
            "live_price": price,
            "links": {"signal": f"/api/terminal/signal/{symbol}"},
        })
    return out


async def get_top_actions(
    ctx: TerminalContext,
    *,
    user_id: str | None = None,
    limit: int = TOP_ACTIONS_MAX_DEFAULT,
) -> dict[str, Any]:
    """Answers Q6: what should the operator act on right now?

    Composes the freshest sovereign decision per symbol with the
    user's open paper_trades. Ranks by conviction × size × freshness,
    classifies each into one of MANAGE_POSITION / ENTER / WATCH /
    EXIT, and returns the top ``limit`` actions plus per-kind totals.

    Read-only and bounded — caps the underlying decision set at 200
    symbols × the 24h lookback window. No fresh API calls, no model
    inference. Empty list on cold-start (no sovereign rows yet) is
    a valid response, never a failure.
    """
    from datetime import timedelta as _td

    now = _now(ctx)
    limit = max(1, min(int(limit if limit is not None else TOP_ACTIONS_MAX_DEFAULT), 50))
    since = now - _td(hours=TOP_ACTIONS_LOOKBACK_HOURS)

    decisions = await _latest_decisions_per_symbol(ctx, since=since)
    open_positions = await _open_positions_for_user(ctx, user_id)

    # Phase 2 EXIT — hard SL/TP breach detection runs BEFORE the
    # sovereign classifier. If a position's live price has already
    # crossed its stop or take, we emit a hard-EXIT card for it
    # (priority 0.99, tier "hard") and *remove* that symbol from the
    # decisions loop so we never render two EXIT cards for the same
    # asset on one tick.
    hard_exit_cards = await _build_hard_exit_cards(ctx, open_positions)
    hard_exit_symbols = {c["symbol"] for c in hard_exit_cards}

    # Surface MANAGE_POSITION cards for every open position even if
    # there's no fresh sovereign decision on that symbol — operator
    # still needs visibility. Symbols that already got a hard-EXIT
    # card are excluded here so they don't get a duplicate orphan
    # MANAGE row.
    positions_without_signal = {
        sym: row for sym, row in open_positions.items()
        if sym not in hard_exit_symbols
        and not any((d.get("symbol") or "").upper() == sym for d in decisions)
    }

    actions: list[dict[str, Any]] = list(hard_exit_cards)
    enter_count = 0

    for d in decisions:
        symbol = (d.get("symbol") or "").upper()
        if not symbol:
            continue
        # Skip decisions for symbols that already got a hard-EXIT —
        # the hard breach wins. Operator closes, period.
        if symbol in hard_exit_symbols:
            continue
        action = (d.get("action") or "HOLD").upper()
        tier = d.get("conviction_tier") or _conviction_label(d.get("confidence"))
        confidence = float(d.get("confidence") or 0.0)
        size_multiplier = float(d.get("size_multiplier") or 0.0)
        vetoes = list(d.get("vetoes") or [])
        has_pos = symbol in open_positions
        pos_direction: str | None = None
        if has_pos:
            pos_row = open_positions[symbol]
            pos_direction = (
                pos_row.get("direction") or pos_row.get("side") or ""
            ).upper() or None

        kind = _classify_kind(
            action=action, tier=tier,
            confidence=confidence, has_open_position=has_pos,
            position_direction=pos_direction,
        )

        # Concentration guard — once we've queued the configured
        # number of ENTER cards, demote subsequent ENTER candidates
        # to WATCH so the operator sees them but doesn't get nudged
        # into over-concentration.
        correlation_note: str | None = None
        if kind == "ENTER" and enter_count >= TOP_ACTIONS_ENTER_CAP:
            kind = "WATCH"
            correlation_note = (
                f"Demoted to WATCH — {TOP_ACTIONS_ENTER_CAP}-position "
                f"concentration cap already reached this tick."
            )
        elif kind == "ENTER":
            enter_count += 1

        created = d.get("created_at")
        if isinstance(created, datetime):
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_min = max(0.0, (now - created).total_seconds() / 60.0)
        else:
            age_min = 0.0

        score = _action_priority_score(
            confidence=confidence,
            tier=tier,
            size_multiplier=size_multiplier,
            decision_age_minutes=age_min,
        )
        # EXIT cards represent a "sovereign reversed against your open
        # position" event — the operator must see this immediately,
        # ahead of any new ENTER candidate. Floor the score at 0.95
        # so the natural ranking always lifts EXIT to the top.
        if kind == "EXIT":
            score = max(score, 0.95)

        actions.append({
            "kind": kind,
            "symbol": symbol,
            "asset_type": d.get("asset_type") or "equity",
            "action": action,
            "priority_score": score,
            "conviction": {"tier": tier, "score": round(confidence, 4)},
            "size_multiplier": round(size_multiplier, 4),
            "vetoes": vetoes,
            "vetoes_count": len(vetoes),
            "reason": _reason_text(
                kind=kind, action=action, tier=tier,
                confidence=confidence, has_open_position=has_pos,
                vetoes_count=len(vetoes),
                position_direction=pos_direction,
            ),
            "correlation_note": correlation_note,
            "decision_id": d.get("decision_id"),
            "decision_age_minutes": round(age_min, 1),
            "shadow": bool(d.get("shadow", True)),
            "links": {"signal": f"/api/terminal/signal/{symbol}"},
        })

    # MANAGE cards for positions without any fresh signal.
    for sym, row in positions_without_signal.items():
        actions.append({
            "kind": "MANAGE_POSITION",
            "symbol": sym,
            "asset_type": row.get("asset_class") or "equity",
            "action": (row.get("direction") or "LONG").upper(),
            "priority_score": 0.50,
            "conviction": {"tier": "unknown", "score": None},
            "size_multiplier": 0.0,
            "vetoes": [],
            "vetoes_count": 0,
            "reason": "Open position — no fresh sovereign view in window",
            "correlation_note": None,
            "decision_id": None,
            "decision_age_minutes": None,
            "shadow": True,
            "links": {"signal": f"/api/terminal/signal/{sym}"},
        })

    actions.sort(key=lambda a: a["priority_score"], reverse=True)
    actions = actions[:limit]
    for i, a in enumerate(actions, start=1):
        a["rank"] = i

    totals = {"manage": 0, "enter": 0, "watch": 0, "exit": 0}
    for a in actions:
        if a["kind"] == "MANAGE_POSITION":
            totals["manage"] += 1
        elif a["kind"] == "ENTER":
            totals["enter"] += 1
        elif a["kind"] == "WATCH":
            totals["watch"] += 1
        elif a["kind"] == "EXIT":
            totals["exit"] += 1

    return {
        "as_of": now.isoformat(),
        "user_id": user_id,
        "limit": limit,
        "lookback_hours": TOP_ACTIONS_LOOKBACK_HOURS,
        "enter_cap": TOP_ACTIONS_ENTER_CAP,
        "totals": totals,
        "actions": actions,
    }
