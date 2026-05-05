"""Conviction calibration & quality KPIs — admin-only.

Extracted from ``routes/admin.py`` (4500+ lines, on the size
allowlist) to keep the admin surface domain-modular. URLs unchanged
so no frontend or test edits are needed:

  * ``GET /api/admin/conviction/calibration``
  * ``GET /api/admin/conviction/quality-kpis``
  * ``GET /api/admin/conviction/clamp-canary``
  * ``GET /api/admin/conviction/reliability``

Read-only. Owner-gated via the same ``_require_owner`` pattern used by
other extracted admin modules (e.g. ``routes/regime_memory.py``).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-conviction"])

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_admin(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def _require_owner(request: Request):
    """Stricter gate than ``_require_admin``: only owner role passes.
    Mirrors the helper in ``routes/admin.py`` — duplicated rather than
    imported so this module has no inbound dependency on admin.py."""
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# Buckets mirror the tiers produced by ``_compute_conviction()`` in
# risk_calculator.py — boundaries chosen so "Strong" reliably trips
# the full-size multiplier and "Weak" reliably trips the 0x veto path.
CONVICTION_BUCKETS = [
    {"label": "Weak",     "min": 0.0,  "max": 0.35, "tier": "weak"},
    {"label": "Moderate", "min": 0.35, "max": 0.65, "tier": "moderate"},
    {"label": "Strong",   "min": 0.65, "max": 1.01, "tier": "strong"},
]

# Fallback buckets for legacy predictions (no conviction attached).
# We bucket by raw ``confidence`` at the same cutoffs so the admin
# can still see a calibration curve while we accumulate
# conviction-tagged data.
CONFIDENCE_BUCKETS = [
    {"label": "Low",    "min": 0.0,  "max": 0.35},
    {"label": "Medium", "min": 0.35, "max": 0.65},
    {"label": "High",   "min": 0.65, "max": 1.01},
]


@router.get("/conviction/calibration")
async def conviction_calibration(request: Request, days: int = 30):
    """Win-rate bucketed by Conviction score (and, as fallback, by raw
    confidence for legacy rows without conviction). Owner-only.

    A healthy model has monotonically rising win-rate across Weak →
    Moderate → Strong. If Moderate wins more than Strong, the
    hand-tuned CONVICTION_WEIGHTS in risk_calculator.py are
    miscalibrated — we should retrain them against actual outcomes.

    Also returns a 4-week trend series (oldest→newest) per bucket so
    the admin UI can sparkline-check for regime drift BEFORE the
    window-wide monotonicity badge flips.
    """
    await _require_owner(request)
    days = max(1, min(int(days), 365))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    # Trend always covers the last 4 ISO-style weeks regardless of
    # ``days`` — gives the sparkline a consistent x-axis while the
    # headline buckets respect the user-selected window.
    trend_weeks = 4
    trend_since = now - timedelta(days=trend_weeks * 7)
    fetch_since = min(since, trend_since)

    cursor = db.predictions.find(
        {
            "verified_24h.correct": {"$in": [True, False]},  # exclude NEUTRAL (null)
            "timestamp": {"$gte": fetch_since.isoformat()},
        },
        {
            "_id": 0,
            "conviction": 1,
            "confidence": 1,
            "verified_24h.correct": 1,
            "timestamp": 1,
        },
    ).limit(10000)

    def _empty(buckets):
        # Include the raw cutoffs during aggregation; stripped in _finalise.
        return [
            {**b, "total": 0, "correct": 0, "win_rate": None}
            for b in buckets
        ]

    by_conviction = _empty(CONVICTION_BUCKETS)
    by_confidence = _empty(CONFIDENCE_BUCKETS)
    # Week 0 = oldest, week 3 = newest. Pre-seed so the sparkline
    # always has 4 points even when a week has zero data.
    weekly_conviction = [_empty(CONVICTION_BUCKETS) for _ in range(trend_weeks)]
    weekly_confidence = [_empty(CONFIDENCE_BUCKETS) for _ in range(trend_weeks)]
    total_verified = 0
    total_with_conviction = 0

    def _bucket_for(value, buckets):
        for b in buckets:
            if b["min"] <= value < b["max"]:
                return b
        return None

    def _week_index(ts_str: str):
        """Map an ISO timestamp → 0..3 week bucket, or None if out of range.

        Uses a naive ``fromisoformat`` + UTC-aware fallback. Older
        Python versions (pre-3.11) can't parse the trailing 'Z'; we
        strip it defensively so the endpoint stays portable.
        """
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            return None
        delta_days = (now - ts).days
        if delta_days < 0 or delta_days >= trend_weeks * 7:
            return None
        # weeks ago: 0..trend_weeks-1 (0 = this week). Flip so newest = last.
        weeks_ago = delta_days // 7
        return (trend_weeks - 1) - weeks_ago

    async for row in cursor:
        ts = row.get("timestamp", "")
        correct = bool((row.get("verified_24h") or {}).get("correct"))
        in_window = ts >= since.isoformat()
        wk = _week_index(ts)

        if in_window:
            total_verified += 1

        conv = (row.get("conviction") or {}).get("score")
        if isinstance(conv, (int, float)):
            if in_window:
                total_with_conviction += 1
                b = _bucket_for(conv, by_conviction)
                if b is not None:
                    b["total"] += 1
                    if correct:
                        b["correct"] += 1
            if wk is not None:
                b = _bucket_for(conv, weekly_conviction[wk])
                if b is not None:
                    b["total"] += 1
                    if correct:
                        b["correct"] += 1

        conf = row.get("confidence")
        if isinstance(conf, (int, float)):
            conf_norm = conf / 100.0 if conf > 1.0 else conf
            if in_window:
                b = _bucket_for(conf_norm, by_confidence)
                if b is not None:
                    b["total"] += 1
                    if correct:
                        b["correct"] += 1
            if wk is not None:
                b = _bucket_for(conf_norm, weekly_confidence[wk])
                if b is not None:
                    b["total"] += 1
                    if correct:
                        b["correct"] += 1

    def _finalise(buckets):
        out = []
        for b in buckets:
            win_rate = round(b["correct"] / b["total"], 4) if b["total"] else None
            out.append({
                "label": b["label"],
                "tier": b.get("tier"),
                "total": b["total"],
                "correct": b["correct"],
                "win_rate": win_rate,
                "range": [b["min"], b["max"]],
            })
        return out

    def _trend_series(weekly_buckets):
        """Pivot [week][bucket] → {bucket_label: [wr_week0, …, wr_week3]}.

        Returns None entries when a week had no data for that bucket
        so the UI can render gaps instead of misleading zero-points.
        """
        if not weekly_buckets:
            return {}
        labels = [b["label"] for b in weekly_buckets[0]]
        series = {lbl: [] for lbl in labels}
        for week in weekly_buckets:
            for b in week:
                wr = round(b["correct"] / b["total"], 4) if b["total"] else None
                series[b["label"]].append(wr)
        return series

    def _week_bounds():
        """Return the [start_iso, end_iso] pair for each of the 4 weeks."""
        out = []
        for i in range(trend_weeks):
            weeks_ago = (trend_weeks - 1) - i
            end = now - timedelta(days=weeks_ago * 7)
            start = end - timedelta(days=7)
            out.append([start.isoformat(), end.isoformat()])
        return out

    by_conv_out = _finalise(by_conviction)
    by_conf_out = _finalise(by_confidence)

    def _is_monotonic(buckets):
        rates = [b["win_rate"] for b in buckets if b["win_rate"] is not None]
        if len(rates) < 2:
            return None
        return all(rates[i] <= rates[i + 1] + 1e-9 for i in range(len(rates) - 1))

    def _ece(buckets):
        """Expected Calibration Error — weighted-mean gap between
        each bucket's midpoint confidence and its actual accuracy.

        Standard formula: ECE = Σ (n_b / N) × |conf_b − acc_b|.

        We use the bucket midpoint as the avg confidence in the
        bucket — slightly imprecise vs. tracking per-prediction
        confidences but standard for binned visualisations and
        directionally identical for monotonicity-style alarms.
        Returns None when fewer than 2 buckets have data.
        """
        observed = [b for b in buckets if b["total"] > 0 and b["win_rate"] is not None]
        if len(observed) < 2:
            return None
        n_total = sum(b["total"] for b in observed)
        if n_total == 0:
            return None
        gap = 0.0
        for b in observed:
            lo, hi = b["range"]
            midpoint = (lo + hi) / 2.0
            gap += (b["total"] / n_total) * abs(midpoint - b["win_rate"])
        return round(gap, 4)

    ece_conv = _ece(by_conv_out)
    ece_conf = _ece(by_conf_out)

    def _calibration_notes():
        """Auto-generated single-sentence operator guidance."""
        out = []
        if total_verified < 30:
            out.append(
                f"Only {total_verified} verified predictions in the window — "
                "calibration numbers are noisy below ~30 samples."
            )
        # ECE thresholds: >0.10 = poor, >0.05 = borderline, else healthy.
        if ece_conv is not None and ece_conv > 0.10:
            out.append(
                f"Conviction ECE = {ece_conv:.3f} (poor); model is overconfident "
                "or underconfident relative to actual win rate."
            )
        elif ece_conv is not None and ece_conv > 0.05:
            out.append(f"Conviction ECE = {ece_conv:.3f} — borderline calibration.")
        if ece_conf is not None and ece_conf > 0.10:
            out.append(
                f"Confidence ECE = {ece_conf:.3f} (poor); raw confidence scores "
                "diverge meaningfully from realised win rate."
            )
        # Monotonicity flags BEFORE ECE wording so the operator sees
        # the structural failure first.
        mono_conv = _is_monotonic(by_conv_out)
        mono_conf = _is_monotonic(by_conf_out)
        if mono_conv is False:
            out.append(
                "Conviction win-rate is non-monotonic (Moderate beats Strong, "
                "or similar) — CONVICTION_WEIGHTS need retraining."
            )
        if mono_conf is False:
            out.append("Confidence win-rate is non-monotonic across buckets.")
        if not out:
            out.append("Calibration is healthy across all buckets.")
        return out

    return {
        "window_days": days,
        "total_verified": total_verified,
        "total_with_conviction": total_with_conviction,
        "by_conviction": by_conv_out,
        "by_confidence": by_conf_out,
        "monotonic": {
            "conviction": _is_monotonic(by_conv_out),
            "confidence": _is_monotonic(by_conf_out),
        },
        # Expected Calibration Error — single-number health metric
        # (lower = better; ≤0.05 healthy, ≤0.10 borderline, >0.10 poor).
        "ece": {
            "conviction": ece_conv,
            "confidence": ece_conf,
        },
        "trend": {
            "weeks": trend_weeks,
            "bounds": _week_bounds(),
            "by_conviction": _trend_series(weekly_conviction),
            "by_confidence": _trend_series(weekly_confidence),
        },
        "notes": _calibration_notes(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/conviction/quality-kpis")
async def conviction_quality_kpis(request: Request, weeks: int = 8):
    """Two operator-facing risk-quality metrics over the last N weeks.

    Returns
    -------
    {
      "weeks": int,
      "unique_failure_patterns": [
         {"week_start": "2026-04-08", "count": int, "patterns": [...]},
         ...  # oldest -> newest
      ],
      "calibration_gap": [
         {"week_start": "2026-04-08", "n": int, "avg_confidence": float,
          "accuracy": float, "gap": float},
         ...
      ],
      "summary": {
         "trend": "improving" | "stable" | "degrading" | "insufficient_data",
         "current_gap": float | None,
         "current_unique_failures": int | None,
      },
    }

    A pattern is ``(symbol, failure_code)``. We count DISTINCT patterns
    per week — the post-cleanup version of the recurring "Toxic
    Spikes" bug where 15× duplicates of NVDA-100% inflated the alert
    count. The fix put dedup on write; this widget shows the result.

    A degrading trend (latest |gap| > earliest |gap| by ≥0.05) is the
    early-warning signal that the ML stack needs retraining.
    """
    await _require_owner(request)
    weeks = max(1, min(int(weeks), 26))
    now = datetime.now(timezone.utc)

    # Align to ISO week starts (Mondays UTC). We bucket by the
    # "Monday-of" date string so all rows in the same calendar week
    # collapse into one entry — UI sparkline gets a uniform x-axis.
    def monday_of(dt):
        return (dt - timedelta(days=dt.weekday())).date()

    earliest_monday = monday_of(now - timedelta(weeks=weeks - 1))
    fetch_since = datetime.combine(
        earliest_monday, datetime.min.time(), tzinfo=timezone.utc,
    )

    cursor = db.predictions.find(
        {
            "verified_24h.correct": {"$in": [True, False]},
            "timestamp": {"$gte": fetch_since.isoformat()},
        },
        {
            "_id": 0,
            "symbol": 1,
            "confidence": 1,
            "verified_24h.correct": 1,
            "verified_24h.failure_code": 1,
            "timestamp": 1,
        },
    ).limit(20000)

    week_keys = [
        (earliest_monday + timedelta(weeks=i)).isoformat()
        for i in range(weeks)
    ]
    failure_buckets: dict[str, set] = {k: set() for k in week_keys}
    cal_buckets: dict[str, dict] = {
        k: {"n": 0, "sum_conf": 0.0, "n_correct": 0} for k in week_keys
    }

    async for row in cursor:
        ts = row.get("timestamp")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        wk = monday_of(dt).isoformat()
        if wk not in failure_buckets:
            continue
        # Confidence is stored mixed 0-1 OR 0-100. Normalise to 0-1
        # so the gap math is in a single scale — same predicate used
        # by ``prediction_tracker.normalize_confidence``.
        conf = float(row.get("confidence") or 0.0)
        if conf > 1.0:
            conf = conf / 100.0
        verified = (row.get("verified_24h") or {})
        correct = verified.get("correct")
        # Calibration gap aggregates EVERY verified row regardless of
        # win/loss — that's the whole point of calibration (compare
        # predicted prob to empirical hit rate).
        cb = cal_buckets[wk]
        cb["n"] += 1
        cb["sum_conf"] += conf
        if correct is True:
            cb["n_correct"] += 1
        # Distinct-failure-pattern set only counts misses (failure
        # patterns by definition can't be wins).
        if correct is False:
            sym = (row.get("symbol") or "?").upper()
            fcode = verified.get("failure_code") or "UNKNOWN"
            failure_buckets[wk].add((sym, fcode))

    unique_failure_patterns = []
    for wk in week_keys:
        patterns = sorted(failure_buckets[wk])
        unique_failure_patterns.append({
            "week_start": wk,
            "count": len(patterns),
            # Truncate sample list — operator only needs a glance at
            # what's recurring, not the full set.
            "patterns": [{"symbol": s, "failure_code": f} for s, f in patterns[:8]],
        })

    calibration_gap = []
    for wk in week_keys:
        b = cal_buckets[wk]
        if b["n"] == 0:
            calibration_gap.append({
                "week_start": wk, "n": 0,
                "avg_confidence": None, "accuracy": None, "gap": None,
            })
            continue
        avg_conf = b["sum_conf"] / b["n"]
        accuracy = b["n_correct"] / b["n"]
        calibration_gap.append({
            "week_start": wk,
            "n": b["n"],
            "avg_confidence": round(avg_conf, 4),
            "accuracy": round(accuracy, 4),
            "gap": round(avg_conf - accuracy, 4),
        })

    # Trend over the populated weeks. We compare the AVG of the first
    # 1/3 of populated weeks to the AVG of the last 1/3 — robust to
    # single-week outliers. < 0.05 absolute change = stable.
    populated = [c for c in calibration_gap if c["gap"] is not None]
    summary_trend = "insufficient_data"
    if len(populated) >= 3:
        third = max(1, len(populated) // 3)
        head_avg = sum(abs(c["gap"]) for c in populated[:third]) / third
        tail_avg = sum(abs(c["gap"]) for c in populated[-third:]) / third
        delta = tail_avg - head_avg
        if delta <= -0.05:
            summary_trend = "improving"
        elif delta >= 0.05:
            summary_trend = "degrading"
        else:
            summary_trend = "stable"

    return {
        "weeks": weeks,
        "unique_failure_patterns": unique_failure_patterns,
        "calibration_gap": calibration_gap,
        "summary": {
            "trend": summary_trend,
            "current_gap": populated[-1]["gap"] if populated else None,
            "current_unique_failures": (
                unique_failure_patterns[-1]["count"]
                if unique_failure_patterns else None
            ),
        },
        "generated_at": now.isoformat(),
    }


@router.get("/conviction/clamp-canary")
async def conviction_clamp_canary(request: Request, days: int = 30):
    """Count prediction outcomes that hit the
    ``score_prediction_outcome`` boundary (±MIN_REWARD / MAX_PENALTY).
    Zero is the healthy state today; any non-zero count means
    ``GRADE_WEIGHTS`` drifted past the clamp or a confidence-scale
    bug is pushing scores beyond bounds."""
    await _require_admin(request)
    from services.conviction_clamp_canary import conviction_clamp_counter
    return await conviction_clamp_counter(db, days=days)


@router.get("/conviction/reliability")
async def conviction_reliability(request: Request, days: int = 30):
    """Classic reliability diagram — hit-rate bucketed by confidence
    decile (0, 10, 20, …, 100). Complements ``/conviction/calibration``
    (which buckets by tier). Includes Expected Calibration Error
    (ECE) so the admin UI can flag "well_calibrated" at a glance."""
    await _require_admin(request)
    from services.calibration_reliability import reliability_snapshot
    days = max(1, min(int(days), 365))
    return await reliability_snapshot(db, days=days)
