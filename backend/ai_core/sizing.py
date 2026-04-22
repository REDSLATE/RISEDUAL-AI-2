"""RISEDUAL AI — Tier 3 adaptive position-sizing engine.

Ports the user-supplied sizing stack into the canonical `ai_core/`
package. Pure functions — no DB, no broker, no state. Call sites
pass in the `readiness` dict (from `services.tier3_readiness`) and
the signal's `prediction` dict; this module returns a final
dollar-notional size to send to the executor.

The two core ideas:
  * **Readiness scaling** — position size tracks `confidence_score`
    (0-100) with safety throttles for strong_miss rate, clamp-canary
    trips, and low high-conf sample size.
  * **Confidence scaling** — each individual signal's confidence
    (0-100, normalised) contributes a multiplier between
    `MIN_CONF_MULT` and `MAX_CONF_MULT`.

Final size = `base_size × readiness_mult × confidence_mult`, capped
at [0.1, 2.0] so a calibration glitch can't blow out a position.

The drop-in hooks `apply_adaptive_position_size` and
`apply_per_trade_sizing` match the user-supplied names verbatim so
call sites can import them without rewriting.

Also exposes `build_tier3_snapshot_message(current, previous)` — a
one-line human-readable summary suitable for Slack/log lines/CLI
(distinct from the HTML email the digest produces).
"""
from __future__ import annotations

from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

# Position-multiplier bounds. The floor (0.25) prevents a zero-reward
# day when the model is technically functional but below the 100-point
# readiness score; the ceiling (1.0) keeps us from over-leveraging
# even at a perfect score.
MAX_POSITION_MULTIPLIER: float = 1.0
MIN_POSITION_MULTIPLIER: float = 0.25

# Confidence-multiplier scaling window. Anything below 50 gets
# clamped to 50 (and then scaled to the floor multiplier); anything
# above 100 hits the ceiling. Linear in between.
MIN_CONFIDENCE: float = 50.0
MAX_CONFIDENCE: float = 100.0
MIN_CONF_MULT: float = 0.3
MAX_CONF_MULT: float = 1.5

# Safety throttles. Each of these halves the readiness multiplier
# (or quarters it, in the clamp canary case) when tripped.
STRONG_MISS_CUTOFF: float = 0.10   # > 10% STRONG_MISS → throttle
CLAMP_CUTOFF: int = 0              # any clamp hit → heavy throttle

# Min confidence to trade at all. Below this the sizing engine
# returns 0.0 — call sites should short-circuit before calling the
# broker.
MIN_CONFIDENCE_TO_TRADE: float = 55.0

# Final-multiplier clamp range. Even with all other guards, we
# refuse to size below 0.1x or above 2x of `base_size`.
_FINAL_MULT_FLOOR: float = 0.1
_FINAL_MULT_CEIL: float = 2.0


# ═══════════════════════════════════════════════════════════════════════════════
# READINESS MULTIPLIER  (readiness dict → 0.0-1.0 scalar)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_position_multiplier(readiness: dict) -> float:
    """Turn a :func:`services.tier3_readiness.tier3_readiness_snapshot`
    result into a position-size multiplier in
    `[MIN_POSITION_MULTIPLIER, MAX_POSITION_MULTIPLIER]`, subject to
    three safety throttles:

      * `strong_miss_rate > 10%`     → ×0.5 (risk-off)
      * `clamp_total > 0`            → ×0.25 (canary trip)
      * `high_conf_trades < 30`      → ×0.75 (small-sample discount)

    Multipliers compound — a run that trips all three ends up at
    ``score/100 × 0.5 × 0.25 × 0.75`` which is deliberately tiny.

    The `readiness` dict is expected to carry a top-level
    ``confidence_score`` and nested ``stats`` dict, matching what
    `tier3_readiness_snapshot()` produces. Missing keys are treated
    as 0 (fail-closed to the smallest safe multiplier).
    """
    score = float(readiness.get("confidence_score", 0))
    stats = readiness.get("stats") or {}

    # Base: normalise score to 0-1 and clamp to the allowed window.
    mult = score / 100.0
    mult = max(min(mult, MAX_POSITION_MULTIPLIER), MIN_POSITION_MULTIPLIER)

    if float(stats.get("strong_miss_rate", 0)) > STRONG_MISS_CUTOFF:
        mult *= 0.5
    if int(stats.get("clamp_total", 0)) > CLAMP_CUTOFF:
        mult *= 0.25
    if int(stats.get("high_conf_trades", 0)) < 30:
        mult *= 0.75

    return round(mult, 3)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIDENCE MULTIPLIER  (0-100 confidence → 0.3-1.5 scalar)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_confidence_multiplier(confidence: float) -> float:
    """Linear ramp from `MIN_CONF_MULT` @ `MIN_CONFIDENCE` to
    `MAX_CONF_MULT` @ `MAX_CONFIDENCE`. Values outside the window
    are clamped. Use in combination with
    :func:`compute_position_multiplier` — this one is per-trade,
    that one is per-day.
    """
    conf = max(min(float(confidence), MAX_CONFIDENCE), MIN_CONFIDENCE)
    norm = (conf - MIN_CONFIDENCE) / (MAX_CONFIDENCE - MIN_CONFIDENCE)
    return round(MIN_CONF_MULT + norm * (MAX_CONF_MULT - MIN_CONF_MULT), 3)


# ═══════════════════════════════════════════════════════════════════════════════
# FINAL SIZING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_final_position_size(
    base_size: float,
    readiness: dict,
    prediction: dict,
    *,
    model_ece: float | None = None,
) -> float:
    """Return the adaptive dollar-notional size for this signal, or
    `0.0` when the signal fails the `MIN_CONFIDENCE_TO_TRADE` gate.

    Computation:
      ``base_size × readiness_mult × confidence_mult × calibration_mult``
    then clamped to `[0.1, 2.0]` of `base_size` as a final belt.

    `prediction` is expected to expose a `confidence` key on the
    0-100 scale (same contract as everywhere else in the pipeline).

    `model_ece` (optional, keyword-only) pulls in the model's latest
    Expected Calibration Error from `SignalModel.calibration_stats`.
    When provided, position size is dampened to reflect the model's
    actual reliability — a miscalibrated model at 70% "confidence"
    shouldn't size like a well-calibrated one. `None` (default)
    preserves legacy behavior so older callers don't regress.
    """
    if float(prediction.get("confidence", 0)) < MIN_CONFIDENCE_TO_TRADE:
        return 0.0

    readiness_mult = compute_position_multiplier(readiness)
    conf_mult = compute_confidence_multiplier(float(prediction.get("confidence", 50)))

    # Calibration dampener (Phase 2 of sizing): applied on TOP of
    # confidence, not replacing it. Well-calibrated models (ECE<5%)
    # get 1.0 — legacy callers unchanged. Badly calibrated ones
    # (ECE≥20%) get 0.4×, matching the `_ECE_FLOOR_MULT` in
    # `ai_core/learning_sizing.py`. When `model_ece` is None (e.g.,
    # no training data yet, or calibration not yet computed), we
    # trust confidence at face value — the readiness gate already
    # prevents untrained-model trading.
    if model_ece is not None:
        from ai_core.learning_sizing import compute_calibration_multiplier
        cal_mult = compute_calibration_multiplier(model_ece)
    else:
        cal_mult = 1.0

    final_mult = readiness_mult * conf_mult * cal_mult
    final_mult = max(min(final_mult, _FINAL_MULT_CEIL), _FINAL_MULT_FLOOR)

    return round(float(base_size) * final_mult, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# DROP-IN HOOKS  (match the user-supplied names exactly)
# ═══════════════════════════════════════════════════════════════════════════════

def apply_adaptive_position_size(base_size: float, readiness: dict) -> float:
    """Readiness-only sizing (ignores per-signal confidence).

    Use at the daily-scheduler level when the loop is deciding
    "today's base allocation" before any specific signal has fired.
    """
    mult = compute_position_multiplier(readiness)
    return round(float(base_size) * mult, 2)


def apply_per_trade_sizing(
    base_size: float,
    readiness: dict,
    prediction: dict,
) -> float:
    """Per-trade sizing. Thin alias over
    :func:`compute_final_position_size` that keeps the verb-first
    naming convention consistent with `execute_trade`,
    `record_outcome`, etc. in the rest of ``ai_core``.
    """
    return compute_final_position_size(base_size, readiness, prediction)


# ═══════════════════════════════════════════════════════════════════════════════
# HUMAN-READABLE SNAPSHOT  (Slack / log lines / CLI)
# ═══════════════════════════════════════════════════════════════════════════════

# Delta arrows. Kept as module constants so the formatter is
# trivially theme-able if a deployment doesn't render unicode.
_ARROW_UP = "▲"
_ARROW_DOWN = "▼"
_ARROW_FLAT = "→"


def _format_blocker(reason: str, stats: dict) -> str:
    """Replace bare reason strings with the actual numbers the owner
    wants to see at a glance. Unknown reasons pass through
    unchanged so we don't lose info if the reason catalog grows."""
    rl = reason.lower()
    if "days" in rl:
        return f"Days {int(stats.get('days', 0))}/30"
    if "trade" in rl:
        return f"Trades {int(stats.get('total_trades', 0))}/100"
    if "high" in rl:
        return f"High-conf {int(stats.get('high_conf_trades', 0))}/30"
    if "miss" in rl:
        rate = float(stats.get("strong_miss_rate", 0)) * 100
        return f"Strong miss {round(rate, 1)}%"
    if "clamp" in rl:
        return "Clamp triggered"
    return reason


def _format_delta(score: float, prev: dict | None) -> str:
    if prev is None:
        return ""
    try:
        prev_score = float(prev.get("confidence_score", 0))
    except (TypeError, ValueError):
        return ""
    delta = round(score - prev_score, 2)
    if delta > 0:
        return f" {_ARROW_UP} +{delta}"
    if delta < 0:
        return f" {_ARROW_DOWN} {delta}"
    return f" {_ARROW_FLAT} 0.0"


def build_tier3_snapshot_message(
    current: dict,
    previous: dict | None = None,
) -> str:
    """Plain-text one-liner Slack/log-friendly snapshot.

    Output example::

        Tier 3 readiness: 81.3 ▲ +1.9 — LOCKED 🔒 | Blockers: Days 7/30, Trades 82/100

    Distinct from `services.tier3_readiness_digest` which builds
    the HTML email body. Use this one for:
      * Slack incoming-webhook payloads
      * `logger.info()` breadcrumbs at the end of every scheduler tick
      * CLI tools / one-off scripts
    """
    score = float(current.get("confidence_score", 0))
    unlocked = bool(current.get("unlocked", False))
    reasons = current.get("reasons") or []
    stats = current.get("stats") or {}

    status = "UNLOCKED ✅" if unlocked else "LOCKED 🔒"
    delta_str = _format_delta(score, previous)

    formatted = [_format_blocker(r, stats) for r in reasons]
    # Keep the line short — first two blockers are usually what the
    # owner is going to action anyway.
    blockers = ", ".join(formatted[:2]) if formatted else "none"

    return (
        f"Tier 3 readiness: {round(score, 1)}{delta_str} — {status} | "
        f"Blockers: {blockers}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION WRAPPER
# ═══════════════════════════════════════════════════════════════════════════════

def execute_trade_with_sizing(
    signal: dict,
    readiness: dict,
    base_size: float = 1000.0,
    df: Any = None,
) -> dict:
    """End-to-end drop-in: compute adaptive size, simulate outcome
    via the canonical intra-bar TP/SL scanner, stamp size onto the
    result.

    Accepts the user-supplied signal dict shape
    (`{entry, tp, sl, direction, confidence}`) and normalises to the
    canonical `Signal` dataclass before simulating.

    Returns `{"skipped": True, "reason": "low confidence", "size": 0.0}`
    when the signal fails the confidence gate.

    For the **live trading path** (real broker order), use the
    richer :class:`ai_core.execution.ExecutionClient` directly — it
    expects typed `Trade` / `Signal` dataclasses and handles broker
    fill callbacks. This helper is for backtests and unit tests
    where a dict signal + DataFrame of future bars is all you have.
    """
    size = compute_final_position_size(base_size, readiness, signal)
    if size == 0.0:
        return {"skipped": True, "reason": "low confidence", "size": 0.0}

    if df is None:
        # No future bars available → caller needs to route through
        # the live ExecutionClient. Return a descriptive stub so
        # the caller notices and doesn't silently "LOSS-count" it.
        return {
            "skipped": False,
            "size": size,
            "result": "PENDING",
            "reason": "live_path_required",
            "exit_price": signal.get("entry"),
            "pnl": 0.0,
        }

    # Normalise the user-supplied dict shape (sl/tp short keys) into
    # the canonical Signal dataclass the simulator expects.
    from .models import Signal
    from .simulator import get_trade_result_from_df

    canonical = Signal(
        asset=signal.get("asset", "UNKNOWN"),
        direction=str(signal.get("direction", "LONG")).upper(),
        entry=float(signal["entry"]),
        stop_loss=float(signal.get("stop_loss", signal.get("sl", 0))),
        take_profit=float(signal.get("take_profit", signal.get("tp", 0))),
        confidence=float(signal.get("confidence", 0)),
    )
    outcome = get_trade_result_from_df(canonical, df, start_index=-1, lookahead=20)
    if not isinstance(outcome, dict):
        outcome = {}
    # The canonical simulator emits `status` ∈ {"win","loss","pending"}.
    # Surface a user-spec `result` key (UPPERCASE "WIN"/"LOSS"/"PENDING")
    # alongside it so the snippet-style "{result, exit_price, pnl}"
    # contract still works for call sites that follow the provided
    # example. Never overwrite an existing `result` key.
    if "result" not in outcome:
        status = str(outcome.get("status", "")).upper()
        if status:
            outcome["result"] = status
    outcome["size"] = size
    return outcome
