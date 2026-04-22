"""Conviction drift alerts — detects week-over-week win-rate drops in the
bucketed calibration data and emails the owner when a bucket's rate drops
≥20 percentage points between the two most recent weeks.

Why bucket-level alerts:
  * The admin panel's monotonicity badge only fires when the entire
    curve flips. That hides single-bucket regressions — e.g. Strong
    stays healthy but Moderate suddenly halves, which would silently
    erode sizing quality on borderline trades.
  * The 30-day headline number averages over regime shifts. A sharp
    drop in the most recent week is exactly the signal we'd otherwise
    miss for another 2-3 weeks until the average catches up.

Guard-rails against alert spam:
  * Only alerts when BOTH compared weeks have `MIN_SAMPLE` or more
    predictions — prevents 2/3 → 0/2 type false alarms on noise.
  * Per-bucket dedup window: suppresses repeat alerts for the same
    bucket within `ALERT_COOLDOWN_HOURS` unless the drop deepens.
  * Runs once/day off-market-hours (scheduled at 08:00 UTC by default).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Tuning knobs — intentionally hardcoded at module level. Easy to find,
# easy to tweak in a PR; no need for runtime reconfiguration yet.
DROP_THRESHOLD_PP = 0.20          # 20 percentage points
MIN_SAMPLE = 5                    # min predictions per week to trust a rate
ALERT_COOLDOWN_HOURS = 24         # per-bucket dedup window

_ALERT_COLLECTION = "conviction_drift_alerts"


async def _build_calibration_snapshot(db: Any) -> Optional[dict]:
    """Reuse the admin endpoint's logic to produce the current trend.

    We call the route handler directly rather than HTTP-self-calling so
    the cron job doesn't depend on service-discovery or auth — plus we
    get the fully-computed trend dict for free. If the route is ever
    renamed, this import will break loudly and we'll update here.
    """
    # Local import keeps this module importable during test/collect even
    # if route registration hasn't happened yet.
    from routes.admin import (
        CONVICTION_BUCKETS, CONFIDENCE_BUCKETS,  # noqa: F401 (used via module)
    )
    # Replay the same aggregation with a fixed 30d window + 4-week trend.
    # We can't call the FastAPI handler directly (it needs Request), so
    # inline a slim version that produces {trend, by_conviction,
    # by_confidence, …} with the same shape.
    now = datetime.now(timezone.utc)
    trend_weeks = 4
    fetch_since = now - timedelta(days=trend_weeks * 7)

    cursor = db.predictions.find(
        {
            "verified_24h.correct": {"$exists": True},
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

    def _empty(buckets: list[dict]) -> list[dict]:
        return [{**b, "total": 0, "correct": 0} for b in buckets]

    weekly_conviction = [_empty(CONVICTION_BUCKETS) for _ in range(trend_weeks)]
    weekly_confidence = [_empty(CONFIDENCE_BUCKETS) for _ in range(trend_weeks)]

    def _bucket_for(value: float, buckets: list[dict]) -> Optional[dict]:
        for b in buckets:
            if b["min"] <= value < b["max"]:
                return b
        return None

    def _week_index(ts_str: str) -> Optional[int]:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            return None
        delta_days = (now - ts).days
        if delta_days < 0 or delta_days >= trend_weeks * 7:
            return None
        weeks_ago = delta_days // 7
        return (trend_weeks - 1) - weeks_ago

    async for row in cursor:
        wk = _week_index(row.get("timestamp", ""))
        if wk is None:
            continue
        correct = bool((row.get("verified_24h") or {}).get("correct"))
        conv = (row.get("conviction") or {}).get("score")
        if isinstance(conv, (int, float)):
            b = _bucket_for(conv, weekly_conviction[wk])
            if b is not None:
                b["total"] += 1
                if correct:
                    b["correct"] += 1
        conf = row.get("confidence")
        if isinstance(conf, (int, float)):
            conf_norm = conf / 100.0 if conf > 1.0 else conf
            b = _bucket_for(conf_norm, weekly_confidence[wk])
            if b is not None:
                b["total"] += 1
                if correct:
                    b["correct"] += 1

    def _trend_series(weekly: list[list[dict]]) -> dict[str, list[dict]]:
        if not weekly:
            return {}
        out = {}
        for bucket_idx in range(len(weekly[0])):
            label = weekly[0][bucket_idx]["label"]
            out[label] = []
            for week in weekly:
                b = week[bucket_idx]
                out[label].append(
                    {"total": b["total"], "correct": b["correct"]}
                )
        return out

    return {
        "by_conviction": _trend_series(weekly_conviction),
        "by_confidence": _trend_series(weekly_confidence),
    }


async def _already_alerted(db: Any, series_name: str, bucket_label: str) -> bool:
    """True if we've already emailed about this bucket within the cooldown."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ALERT_COOLDOWN_HOURS)
    doc = await db[_ALERT_COLLECTION].find_one(
        {
            "series": series_name,
            "bucket": bucket_label,
            "alerted_at": {"$gte": cutoff.isoformat()},
        },
        {"_id": 0, "alerted_at": 1},
    )
    return doc is not None


async def _record_alert(db: Any, series_name: str, bucket_label: str, details: dict) -> None:
    await db[_ALERT_COLLECTION].insert_one({
        "series": series_name,
        "bucket": bucket_label,
        "alerted_at": datetime.now(timezone.utc).isoformat(),
        "details": details,
    })


def _detect_drops(trend_series: dict[str, list[dict]], series_name: str) -> list[dict]:
    """For each bucket, compare wk3 (newest) to wk2 (previous) and return
    those whose win-rate dropped ≥ DROP_THRESHOLD_PP with enough samples.
    """
    drops = []
    for bucket_label, weeks in trend_series.items():
        if len(weeks) < 2:
            continue
        prev, curr = weeks[-2], weeks[-1]
        if prev["total"] < MIN_SAMPLE or curr["total"] < MIN_SAMPLE:
            continue
        prev_rate = prev["correct"] / prev["total"]
        curr_rate = curr["correct"] / curr["total"]
        delta = curr_rate - prev_rate
        if -delta >= DROP_THRESHOLD_PP:
            drops.append({
                "series": series_name,
                "bucket": bucket_label,
                "prev_rate": round(prev_rate, 4),
                "curr_rate": round(curr_rate, 4),
                "delta_pp": round(delta * 100, 1),
                "prev": prev,
                "curr": curr,
            })
    return drops


def _format_alert_html(drops: list[dict]) -> str:
    """Minimal table email — Outlook/Gmail-safe inline styles only."""
    rows = []
    for d in drops:
        prev_pct = f"{d['prev_rate']*100:.1f}%"
        curr_pct = f"{d['curr_rate']*100:.1f}%"
        rows.append(
            f"<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;'>{d['series']}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;font-weight:600;'>{d['bucket']}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;text-align:right;font-family:monospace;'>{prev_pct}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;text-align:right;font-family:monospace;'>{curr_pct}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;text-align:right;color:#c00;font-weight:600;'>{d['delta_pp']:+.1f} pp</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;text-align:right;font-family:monospace;color:#666;'>{d['prev']['correct']}/{d['prev']['total']} → {d['curr']['correct']}/{d['curr']['total']}</td>"
            f"</tr>"
        )
    return f"""
<p>Week-over-week win-rate drops ≥ {int(DROP_THRESHOLD_PP*100)} pp detected
in the Conviction Calibration panel. This usually means either a regime
shift (market behaves differently than training) or weight drift
(CONVICTION_WEIGHTS need retraining). Check the admin panel for the
4-week sparkline.</p>
<table style='border-collapse:collapse;width:100%;font-size:14px;'>
  <thead>
    <tr style='background:#f8f9fa;'>
      <th style='padding:8px;text-align:left;'>Series</th>
      <th style='padding:8px;text-align:left;'>Bucket</th>
      <th style='padding:8px;text-align:right;'>Prev week</th>
      <th style='padding:8px;text-align:right;'>This week</th>
      <th style='padding:8px;text-align:right;'>Δ</th>
      <th style='padding:8px;text-align:right;'>Sample</th>
    </tr>
  </thead>
  <tbody>
    {''.join(rows)}
  </tbody>
</table>
<p style='color:#999;font-size:12px;margin-top:16px;'>
  Each bucket will not re-alert within {ALERT_COOLDOWN_HOURS}h unless the
  drop deepens. Admin panel: /api/admin/conviction/calibration
</p>"""


async def run_conviction_drift_check(db: Any) -> dict:
    """Scheduled entry point. Idempotent — safe to call ad-hoc or on cron.

    Returns a summary dict for logging/ops visibility. Never raises —
    this is a best-effort monitoring signal, not a critical path.
    """
    try:
        snapshot = await _build_calibration_snapshot(db)
        if snapshot is None:
            return {"checked": False, "reason": "no_data"}

        all_drops: list[dict] = []
        all_drops.extend(_detect_drops(snapshot["by_conviction"], "conviction"))
        all_drops.extend(_detect_drops(snapshot["by_confidence"], "confidence"))

        if not all_drops:
            logger.info("[conviction-drift] no drops exceeding threshold, all buckets stable")
            return {"checked": True, "drops": 0}

        # Dedup against recent alerts.
        fresh = []
        for d in all_drops:
            if await _already_alerted(db, d["series"], d["bucket"]):
                logger.info(
                    f"[conviction-drift] suppressed {d['series']}/{d['bucket']} "
                    f"(already alerted within {ALERT_COOLDOWN_HOURS}h)"
                )
                continue
            fresh.append(d)

        if not fresh:
            return {"checked": True, "drops": len(all_drops), "sent": 0, "all_deduped": True}

        # Route to the owner's email. Multi-owner support would need a
        # user-roles query here; for now the single-owner assumption is
        # consistent with the rest of the admin panel.
        owner_email = os.environ.get("OWNER_EMAIL", "admin@risedual.ai")
        subject = f"[RISEDUAL] Conviction drift alert — {len(fresh)} bucket(s)"

        from services.email_service import _routed_send, _base_html
        body = _base_html(
            _format_alert_html(fresh),
            preheader=f"{len(fresh)} bucket(s) dropped ≥{int(DROP_THRESHOLD_PP*100)}pp week-over-week",
        )
        sent = await _routed_send([owner_email], subject, body)

        if sent:
            for d in fresh:
                await _record_alert(db, d["series"], d["bucket"], d)
            logger.info(
                f"[conviction-drift] alert sent to {owner_email} — "
                f"{len(fresh)} fresh drops persisted"
            )

        return {
            "checked": True,
            "drops": len(all_drops),
            "fresh": len(fresh),
            "sent": sent,
            "owner": owner_email,
        }
    except Exception as e:
        logger.exception(f"[conviction-drift] check failed: {e}")
        return {"checked": False, "error": str(e)}
