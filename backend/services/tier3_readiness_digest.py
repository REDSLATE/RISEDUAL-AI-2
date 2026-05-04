"""Owner-facing Tier 3 readiness daily digest.

Emails a one-line ML readiness pulse to the owner every morning:

    Tier 3 readiness: 83.2 ▲ +1.9 — 2 blockers: [live days 8/30, trades 88/100]

Turns the 30-day unlock gate into a visible streak and catches
regressions the day they happen rather than the day someone
remembers to open the admin panel.

Design rules
------------
* Fire-and-forget: any failure logs and returns — never raises.
* Idempotent per day: records the snapshot to
  `tier3_readiness_history` so re-runs on the same UTC date don't
  send a duplicate email.
* Delta is computed against the most recent history row whose
  `date` differs from today's UTC date — so the "▲ +1.9" number
  is always "vs the last day we sent one" instead of "vs the last
  run within the same day".
* No templating engine — a single-line email wrapped in the
  existing `_base_html()` shell keeps the payload under 2kb.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from services.tier3_readiness import tier3_readiness_snapshot

logger = logging.getLogger(__name__)

_HISTORY_COLL = "tier3_readiness_history"


def _utc_date_str(dt: datetime | None = None) -> str:
    d = dt or datetime.now(timezone.utc)
    return d.strftime("%Y-%m-%d")


async def _latest_prior_snapshot(db: Any, today: str) -> dict | None:
    """Return the most recent history row from a DIFFERENT UTC date."""
    try:
        cursor = (
            db[_HISTORY_COLL]
            .find({"date": {"$ne": today}}, {"_id": 0})
            .sort("date", -1)
            .limit(1)
        )
        rows = await cursor.to_list(length=1)
        return rows[0] if rows else None
    except Exception as exc:
        logger.warning("[tier3-digest] prior-snapshot lookup failed: %s", exc)
        return None


async def _snapshot_for_today_exists(db: Any, today: str) -> bool:
    try:
        found = await db[_HISTORY_COLL].find_one(
            {"date": today}, projection={"_id": 1}
        )
        return found is not None
    except Exception as exc:
        logger.warning("[tier3-digest] today-snapshot lookup failed: %s", exc)
        return False


async def _record_snapshot(
    db: Any,
    today: str,
    score: float,
    stats: dict,
    unlock: dict,
) -> None:
    """Idempotent upsert — re-running the digest twice in one UTC day
    overwrites the existing row rather than creating a duplicate."""
    try:
        await db[_HISTORY_COLL].update_one(
            {"date": today},
            {
                "$set": {
                    "date": today,
                    "score": float(score),
                    "stats": stats,
                    "unlock": unlock,
                    "recorded_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
    except Exception as exc:
        logger.warning("[tier3-digest] snapshot record failed: %s", exc)


def _format_blocker_chip(reason: str, stats: dict) -> str:
    """Turn a bare reason string into a "reason: current/target" chip.

    Enriches the three sample-size reasons with the actual numbers —
    those are the ones the owner wants to see every morning.
    """
    days = int(stats.get("days", 0))
    trades = int(stats.get("total_trades", 0))
    hc = int(stats.get("high_conf_trades", 0))
    if reason == "Insufficient live days":
        return f"live days {days}/30"
    if reason == "Insufficient trade count":
        return f"trades {trades}/100"
    if reason == "Not enough high-confidence samples":
        return f"high-conf samples {hc}/30"
    return reason


def _format_subject(score: float, delta: float | None, unlocked: bool) -> str:
    if unlocked:
        return f"[RISEDUAL] Tier 3 UNLOCKED — readiness {score:.1f}/100"
    arrow = ""
    if delta is not None:
        if delta > 0.05:
            arrow = f" ▲ +{delta:.1f}"
        elif delta < -0.05:
            arrow = f" ▼ {delta:.1f}"
        else:
            arrow = " ·"
    return f"[RISEDUAL] Tier 3 readiness: {score:.1f}/100{arrow}"


def _format_body_html(
    score: float,
    delta: float | None,
    stats: dict,
    unlock: dict,
    raw_view: dict | None = None,
    calibration: dict | None = None,
) -> str:
    reasons = list(unlock.get("reasons") or [])
    chips = [_format_blocker_chip(r, stats) for r in reasons]
    chip_line = ", ".join(chips) if chips else "all 6 gates clear"

    delta_str = ""
    if delta is not None:
        if delta > 0.05:
            delta_str = f"<span style='color:#10B981'>▲ +{delta:.1f}</span> vs yesterday"
        elif delta < -0.05:
            delta_str = f"<span style='color:#F59E0B'>▼ {delta:.1f}</span> vs yesterday"
        else:
            delta_str = "<span style='color:#64748B'>— unchanged vs yesterday</span>"
    else:
        delta_str = "<span style='color:#64748B'>(first snapshot — no prior comparison)</span>"

    unlocked_badge = (
        "<div style='font-size:14px;color:#10B981;font-weight:700;margin:6px 0 10px;'>"
        "✅ ALL 6 GATES CLEAR — live execution approved</div>"
        if unlock.get("unlocked")
        else ""
    )

    # Raw vs calibrated badge — only rendered when a calibration
    # model is active. Spells out exactly what each number means so
    # the operator can read it cold without context. ``score`` is
    # the calibrated-derived headline (already what every Tier 3
    # consumer reads); ``raw_view`` carries the uncalibrated peer.
    calibration_badge = ""
    if calibration and calibration.get("active") and raw_view:
        raw_score = float((raw_view.get("unlock") or {}).get("confidence_score", 0.0))
        ece_after = calibration.get("ece_after_pp")
        ece_before = calibration.get("ece_before_pp")
        ece_line = ""
        if ece_after is not None and ece_before is not None:
            ece_line = (
                f" · ECE {ece_before:.1f}pp → {ece_after:.1f}pp "
                f"(scope: tier3 readiness only)"
            )
        calibration_badge = (
            "<div style='display:inline-block;background:#F1F5F9;"
            "border:1px solid #E2E8F0;border-radius:6px;padding:6px 10px;"
            "font-size:12px;color:#334155;margin:0 0 12px;'>"
            f"<strong>{score:.1f}</strong> calibrated &nbsp;·&nbsp; "
            f"<span style='color:#64748B'>{raw_score:.1f} raw</span>"
            f"{ece_line}"
            "</div>"
        )

    return f"""
<h2 style="color:#0F172A;font-size:20px;margin:0 0 8px;font-weight:800;">
  Tier 3 readiness: <span style="color:#0052FF">{score:.1f} / 100</span>
</h2>
<p style="color:#64748B;font-size:13px;margin:0 0 6px;">{delta_str}</p>
{calibration_badge}
{unlocked_badge}
<p style="color:#0F172A;font-size:14px;margin:14px 0 4px;font-weight:600;">
  Blockers ({len(reasons)}):
</p>
<p style="color:#334155;font-size:13px;margin:0 0 14px;">{chip_line}</p>
<table cellpadding="0" cellspacing="0" border="0" style="font-size:12px;color:#475569;">
  <tr><td style="padding:2px 14px 2px 0;">Live days</td><td>{int(stats.get('days', 0))}/30</td></tr>
  <tr><td style="padding:2px 14px 2px 0;">Trades</td><td>{int(stats.get('total_trades', 0))}/100</td></tr>
  <tr><td style="padding:2px 14px 2px 0;">High-conf samples</td><td>{int(stats.get('high_conf_trades', 0))}/30</td></tr>
  <tr><td style="padding:2px 14px 2px 0;">High-conf win-rate</td><td>{stats.get('high_conf_win_rate', 0):.1%}</td></tr>
  <tr><td style="padding:2px 14px 2px 0;">Strong-miss rate</td><td>{stats.get('strong_miss_rate', 0):.1%}</td></tr>
  <tr><td style="padding:2px 14px 2px 0;">Last-7d win-rate</td><td>{stats.get('last_7d_win_rate', 0):.1%}</td></tr>
  <tr><td style="padding:2px 14px 2px 0;">Clamp canary</td><td>{stats.get('clamp_total', 0)}</td></tr>
</table>
"""


async def run_tier3_readiness_digest(db: Any) -> dict:
    """Scheduled entry point. Never raises — logs and returns a
    summary dict suitable for the scheduler log.
    """
    try:
        snap = await tier3_readiness_snapshot(db, days=30)
        stats = snap.get("stats") or {}
        unlock = snap.get("unlock") or {}
        score = float(unlock.get("confidence_score", 0.0))

        today = _utc_date_str()
        if await _snapshot_for_today_exists(db, today):
            logger.info("[tier3-digest] already sent for %s — skipping", today)
            await _record_snapshot(db, today, score, stats, unlock)
            return {"sent": False, "reason": "already_sent_today", "date": today}

        prior = await _latest_prior_snapshot(db, today)
        delta = None
        if prior and isinstance(prior.get("score"), (int, float)):
            delta = round(score - float(prior["score"]), 2)

        owner_email = os.environ.get("OWNER_EMAIL", "admin@risedual.ai")
        subject = _format_subject(score, delta, bool(unlock.get("unlocked")))

        from services.email_service import _base_html, _routed_send
        preheader = (
            f"{score:.1f}/100 · {len(unlock.get('reasons') or [])} blockers"
        )
        html = _base_html(
            _format_body_html(
                score, delta, stats, unlock,
                raw_view=snap.get("raw_view"),
                calibration=snap.get("calibration"),
            ),
            preheader=preheader,
        )
        sent = await _routed_send([owner_email], subject, html)

        # Always record today's snapshot — even when the email failed
        # to deliver — so tomorrow's delta is computed against truth.
        await _record_snapshot(db, today, score, stats, unlock)

        logger.info(
            "[tier3-digest] score=%.1f delta=%s sent=%s owner=%s",
            score,
            delta,
            sent,
            owner_email,
        )
        return {
            "sent": bool(sent),
            "score": score,
            "delta": delta,
            "blockers": len(unlock.get("reasons") or []),
            "unlocked": bool(unlock.get("unlocked")),
            "owner": owner_email,
            "date": today,
        }
    except Exception as exc:
        logger.exception("[tier3-digest] run failed: %s", exc)
        return {"sent": False, "error": str(exc)}
