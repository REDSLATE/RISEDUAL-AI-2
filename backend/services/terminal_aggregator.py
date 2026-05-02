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
) -> list[dict[str, Any]]:
    """Risks come from three sources that each own one axis of ground
    truth: the signal itself (volatility / calibration risk flags), the
    options aggregate (liquidity regime), and position context
    (correlation / double-exposure). We never mutate — always rebuild."""
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
        "risks": _build_risks(signal_doc, option_row, position_context),
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
        "position_context": position_context or None,
        "updated_at": now.isoformat(),
    }
