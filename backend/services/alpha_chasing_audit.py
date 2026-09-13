"""Chasing-filter forensic audit.

The operator's 24/24 chasing rejection finding required a diagnostic
that distinguishes:

* **clearly_extended**   — move so large the cap is doing exactly what
                           it should. Loosening would recreate the
                           late-entry / bad-entry regression.
* **marginally_over**    — move only slightly past cap; the number
                           itself may be miscalibrated for that
                           pattern. Candidate for a modest widening.
* **stale_signal**       — move was inside cap AT signal time but the
                           intent reached the executor too late and
                           the price extended further while the
                           pipeline processed it. Fix the pipeline
                           latency, not the cap.
* **reference_anomaly**  — the ``today_close`` anchor disagrees with
                           the ``signal_price`` by more than the cap.
                           Suggests split/dividend desync or a
                           provider-mismatch bug. Fix the reference.
* **insufficient_data**  — payload lacks signal_price / signal_age /
                           reference_price so we can't classify.
                           Older rows before the audit enrichment
                           land here; new rows should not.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# Current per-pattern effective caps. The global env cap
# ``PUBLIC_LIVE_MAX_INTRADAY_MOVE_PCT`` applies to every pattern; the
# direction class + knife multiplier decide how it's compared.
def current_caps(env_cap_pct: float) -> dict[str, dict[str, Any]]:
    """Return the effective chasing envelope per pattern label so the
    admin panel can render the exact numbers Alpha is enforcing."""
    return {
        # Momentum patterns — reject positive move ≥ env_cap.
        "VWAP_RECLAIM":     {"direction": "momentum", "upper_cap_pct": env_cap_pct, "lower_knife_pct": None},
        "HOD_BREAK":        {"direction": "momentum", "upper_cap_pct": env_cap_pct, "lower_knife_pct": None},
        "BREAKOUT":         {"direction": "momentum", "upper_cap_pct": env_cap_pct, "lower_knife_pct": None},
        "MOMENTUM_REACCEL": {"direction": "momentum", "upper_cap_pct": env_cap_pct, "lower_knife_pct": None},
        # Dip-buy patterns — reject positive move ≥ env_cap OR
        # negative move ≤ -2× env_cap (knife guard).
        "PULLBACK":              {"direction": "dip_buy", "upper_cap_pct": env_cap_pct, "lower_knife_pct": -2 * env_cap_pct},
        "MEAN_REVERSION":        {"direction": "dip_buy", "upper_cap_pct": env_cap_pct, "lower_knife_pct": -2 * env_cap_pct},
        "DOUBLE_BOTTOM":         {"direction": "dip_buy", "upper_cap_pct": env_cap_pct, "lower_knife_pct": -2 * env_cap_pct},
        "INVERSE_HEAD_SHOULDERS":{"direction": "dip_buy", "upper_cap_pct": env_cap_pct, "lower_knife_pct": -2 * env_cap_pct},
        "FALLING_WEDGE":         {"direction": "dip_buy", "upper_cap_pct": env_cap_pct, "lower_knife_pct": -2 * env_cap_pct},
        # SHORT_SIDE_EXHAUSTION widens the knife to -3× so the pattern's
        # designed -3% to -12% sweet spot isn't cut off.
        "SHORT_SIDE_EXHAUSTION": {"direction": "dip_buy", "upper_cap_pct": env_cap_pct, "lower_knife_pct": -3 * env_cap_pct},
    }


# Buckets for post-hoc classification.
STALE_SIGNAL_AGE_SECONDS = 180.0  # signal older than 3 min counts as late
MARGINAL_MULTIPLIER = 1.5          # <1.5× cap → marginally over
REFERENCE_ANOMALY_TOLERANCE_PCT = 2.0  # signal_price vs today_close


def classify_rejection(detail: dict[str, Any]) -> str:
    """Given a chasing_filter skip payload, return the audit bucket.

    ``detail`` is the enriched payload written by
    ``public_equity_live_executor._log_skip``. Pre-enrichment rows
    (before 2026-02) only carry ``move_pct`` + ``cap_pct`` +
    ``setup_type`` — those still classify as clearly_extended vs
    marginally_over based on the move-to-cap ratio, so historical
    data remains useful. Stale-signal and reference-anomaly buckets
    require the enrichment and only apply to newer rows.
    """
    d = detail or {}
    move_pct = d.get("move_pct")
    cap_pct = d.get("cap_pct")

    # Absolute minimum for any classification.
    if move_pct is None or cap_pct is None:
        return "insufficient_data"
    try:
        move_abs = abs(float(move_pct))
        cap = float(cap_pct)
    except (TypeError, ValueError):
        return "insufficient_data"
    if cap <= 0:
        return "insufficient_data"

    signal_price = d.get("signal_price")
    move_at_signal_pct = d.get("move_at_signal_pct")
    signal_age_seconds = d.get("signal_age_seconds")
    reference_price = d.get("reference_price")
    extreme_verdict = d.get("extreme_move_verdict") or {}

    # ── Extreme-move validator takes highest precedence ──
    # If the validator explicitly says the anchor is broken, that's
    # our answer regardless of the coarse move/cap ratio.
    _ev = str(extreme_verdict.get("verdict") or "") if isinstance(extreme_verdict, dict) else ""
    if _ev == "REFERENCE_PRICE_ANOMALY":
        return "reference_anomaly"
    if _ev == "EXTREME_MOVE_CONFIRMED":
        return "clearly_extended"
    # ``EXTREME_MOVE_UNVERIFIED`` falls through to normal classification.

    # Rich enrichment path — full classification available.
    has_enrichment = (
        signal_price is not None
        and move_at_signal_pct is not None
        and reference_price is not None
    )
    if has_enrichment:
        try:
            move_at_signal_abs = abs(float(move_at_signal_pct))
        except (TypeError, ValueError):
            move_at_signal_abs = None

        # Reference anomaly — signal_price and today's close disagree
        # by more than the cap itself. Anchor is broken.
        try:
            _sp = float(signal_price)
            _rp = float(reference_price)
            if _sp > 0 and _rp > 0 and move_at_signal_abs is not None:
                spread_pct = abs((_sp - _rp) / _rp * 100.0) - move_at_signal_abs
                if abs(spread_pct) > REFERENCE_ANOMALY_TOLERANCE_PCT:
                    return "reference_anomaly"
        except (TypeError, ValueError):
            pass

        # Stale signal — was inside cap at signal time, extended past it
        # while pipeline processed the intent.
        if (
            move_at_signal_abs is not None
            and move_at_signal_abs < cap
            and signal_age_seconds is not None
            and float(signal_age_seconds) > STALE_SIGNAL_AGE_SECONDS
        ):
            return "stale_signal"

    # Coarse classification (works on both pre- and post-enrichment).
    if move_abs >= cap * MARGINAL_MULTIPLIER:
        return "clearly_extended"
    return "marginally_over"


async def audit_rejections(
    db: Any,
    *,
    env_cap_pct: float,
    since_seconds: int = 86400,
    limit: int = 500,
) -> dict[str, Any]:
    """Read chasing_filter rejections from ``intent_skip_log`` in the
    window, classify each into a bucket, and return per-bucket counts
    plus per-rejection detail (capped by ``limit``).

    Returns a shape safe to render in the admin panel:

        {
          "since_seconds": 86400,
          "env_cap_pct": 4.0,
          "caps_by_pattern": {...},
          "total": 24,
          "buckets": {
            "clearly_extended": {"count": 9, "sample": [{...}, ...]},
            "marginally_over":  {"count": 7, "sample": [{...}, ...]},
            "stale_signal":     {"count": 5, "sample": [{...}, ...]},
            "reference_anomaly":{"count": 3, "sample": [{...}, ...]},
            "insufficient_data":{"count": 0, "sample": []},
          },
          "recommendation": "<one-line takeaway>",
        }
    """
    if db is None:
        return {"total": 0, "reason": "no_db"}

    since = max(60, min(int(since_seconds or 86400), 30 * 24 * 3600))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=since)

    buckets: dict[str, dict[str, Any]] = {
        "clearly_extended":  {"count": 0, "sample": []},
        "marginally_over":   {"count": 0, "sample": []},
        "stale_signal":      {"count": 0, "sample": []},
        "reference_anomaly": {"count": 0, "sample": []},
        "insufficient_data": {"count": 0, "sample": []},
    }

    try:
        cursor = db.intent_skip_log.find(
            {"reason": "chasing_filter", "ts": {"$gte": cutoff}},
            sort=[("ts", -1)],
        ).limit(limit)
        rows = await cursor.to_list(length=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[chasing_audit] scan failed: %s", exc)
        return {"total": 0, "reason": f"scan_error:{exc.__class__.__name__}"}

    for row in rows:
        detail = row.get("detail") or {}
        bucket = classify_rejection(detail)
        b = buckets[bucket]
        b["count"] += 1
        if len(b["sample"]) < 10:
            b["sample"].append({
                "symbol": row.get("symbol"),
                "ts": row.get("ts").isoformat() if isinstance(row.get("ts"), datetime) else str(row.get("ts")),
                "first_seen": row.get("first_seen").isoformat() if isinstance(row.get("first_seen"), datetime) else None,
                "last_seen": row.get("last_seen").isoformat() if isinstance(row.get("last_seen"), datetime) else None,
                "refire_count": row.get("refire_count", 0),
                "setup_type": detail.get("setup_type"),
                "direction_class": detail.get("direction_class"),
                "move_pct": detail.get("move_pct"),
                "cap_pct": detail.get("cap_pct"),
                "excess_over_cap": detail.get("excess_over_cap"),
                "signal_price": detail.get("signal_price"),
                "reference_price": detail.get("reference_price"),
                "today_close": detail.get("today_close"),
                "move_at_signal_pct": detail.get("move_at_signal_pct"),
                "adverse_drift_since_signal_pct": detail.get("adverse_drift_since_signal_pct"),
                "signal_age_seconds": detail.get("signal_age_seconds"),
                "extreme_move_verdict":
                    (detail.get("extreme_move_verdict") or {}).get("verdict")
                    if isinstance(detail.get("extreme_move_verdict"), dict) else None,
            })

    total = sum(b["count"] for b in buckets.values())
    # Sum of amplification across every unique row's refire_count.
    total_observations = total
    for row in rows:
        rf = row.get("refire_count") or 0
        try:
            total_observations += int(rf)
        except (TypeError, ValueError):
            pass
    return {
        "since_seconds": since,
        "env_cap_pct": env_cap_pct,
        "caps_by_pattern": current_caps(env_cap_pct),
        "total": total,                          # unique rejection events
        "total_observations": total_observations,  # unique + refires
        "amplification_factor": (
            round(total_observations / total, 2) if total else 0.0
        ),
        "buckets": buckets,
        "recommendation": _recommend(buckets, total),
    }


def _recommend(buckets: dict[str, dict[str, Any]], total: int) -> str:
    """One-sentence takeaway to guide the operator's next lever."""
    if total == 0:
        return "No chasing_filter rejections in window."

    def _pct(k: str) -> float:
        return (buckets[k]["count"] / total) * 100.0 if total else 0.0

    ce = _pct("clearly_extended")
    mo = _pct("marginally_over")
    stale = _pct("stale_signal")
    anomaly = _pct("reference_anomaly")
    insuff = _pct("insufficient_data")

    if insuff > 50:
        return (
            f"{insuff:.0f}% of rejections lack the audit payload. Wait for "
            "post-enrichment data before re-classifying."
        )
    if anomaly > 30:
        return (
            f"{anomaly:.0f}% show reference-price anomalies. Investigate "
            "the today_close vs signal_price disagreement BEFORE touching "
            "the cap — the anchor is likely broken."
        )
    if stale > 30:
        return (
            f"{stale:.0f}% were fine at signal time and extended while the "
            "pipeline processed them. Fix pipeline latency (setup → intent "
            "→ executor), not the cap."
        )
    if ce > 60:
        return (
            f"{ce:.0f}% are clearly extended (≥ 1.5× cap). The cap is doing "
            "its job — Alpha is finding trades too late. Investigate "
            "discovery timing, not the filter."
        )
    if mo > 40 and ce < 30:
        return (
            f"{mo:.0f}% are marginally over cap (<1.5× cap). A modest cap "
            "widening (e.g. 1.2×) could unblock them without recreating "
            "the late-entry regression."
        )
    return "Mixed rejection signal — no single lever dominates."


__all__ = ["classify_rejection", "audit_rejections", "current_caps"]
