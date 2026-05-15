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
from datetime import datetime, timedelta, timezone
from typing import Any

from services.tier3_readiness import (
    compute_tier3_breakdown,
    tier3_readiness_snapshot,
)

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


def _compute_gate_movers(
    *, current_stats: dict, prior_stats: dict | None,
) -> list[dict]:
    """Diff per-gate contributions between today's stats and the most
    recent prior snapshot. Returns the gates that actually moved
    (``|delta_pts| ≥ 0.5``), sorted by absolute magnitude.

    This is the "why did the score drop" panel: each entry carries
    the gate label, its prior and current ``earned_pts``, and the
    signed delta. Empty list means no gate moved meaningfully.
    """
    if not prior_stats:
        return []
    cur = {b["key"]: b for b in compute_tier3_breakdown(current_stats)}
    prv = {b["key"]: b for b in compute_tier3_breakdown(prior_stats)}
    out: list[dict] = []
    for key, cur_row in cur.items():
        prv_row = prv.get(key)
        if not prv_row:
            continue
        delta = round(float(cur_row["earned_pts"]) - float(prv_row["earned_pts"]), 2)
        if abs(delta) < 0.5:
            continue
        out.append({
            "key": key,
            "label": cur_row["label"],
            "prior_pts": float(prv_row["earned_pts"]),
            "current_pts": float(cur_row["earned_pts"]),
            "delta_pts": delta,
            "current_hint": cur_row.get("hint", ""),
            "prior_hint": prv_row.get("hint", ""),
        })
    out.sort(key=lambda r: abs(r["delta_pts"]), reverse=True)
    return out


def _format_calendar_context(stats: dict) -> str:
    """Render the "6/30 days across 29-day calendar window" hint so
    the operator can see at a glance whether they're trading densely
    or sparsely. Returns "" when the trading history is empty."""
    first = stats.get("first_trade_at")
    last = stats.get("last_trade_at")
    window = int(stats.get("window_days") or 0)
    days = int(stats.get("days") or 0)
    if not first or not last or window == 0:
        return ""
    sparsity = days / window if window > 0 else 1.0
    sparsity_note = ""
    if days >= 30:
        sparsity_note = ""
    elif sparsity < 0.4:
        sparsity_note = (
            " <span style='color:#F59E0B'>(sparse — most calendar days "
            "have no trades)</span>"
        )
    elif sparsity < 0.7:
        sparsity_note = (
            " <span style='color:#64748B'>(intermittent — gap days "
            "do not count toward Tier 3)</span>"
        )
    try:
        first_str = first[:10] if isinstance(first, str) else first.strftime("%Y-%m-%d")
        last_str = last[:10] if isinstance(last, str) else last.strftime("%Y-%m-%d")
    except (AttributeError, TypeError):
        return ""
    return (
        f"<p style='color:#64748B;font-size:12px;margin:-4px 0 12px;'>"
        f"{days}/30 across {window}-day calendar window "
        f"({first_str} → {last_str}){sparsity_note}"
        f"</p>"
    )


def _format_gate_movers_block(movers: list[dict], delta_days: int | None) -> str:
    """Render the per-gate "what moved" panel. When no prior snapshot
    or no meaningful moves, returns "". When `delta_days` > 1, also
    note that the comparison is vs a non-adjacent day so the operator
    knows the "▼" arrow isn't a literal 24-hour change."""
    if not movers:
        return ""

    rows_html: list[str] = []
    for m in movers[:5]:  # top 5 movers, keeps email scannable
        delta = m["delta_pts"]
        if delta > 0:
            color = "#10B981"
            arrow = "▲ +"
        else:
            color = "#F59E0B"
            arrow = "▼ "
        rows_html.append(
            "<tr>"
            f"<td style='padding:3px 14px 3px 0;color:#0F172A;'>{m['label']}</td>"
            f"<td style='padding:3px 14px 3px 0;color:#64748B;'>"
            f"{m['prior_pts']:.1f} → {m['current_pts']:.1f} pts</td>"
            f"<td style='padding:3px 0;color:{color};font-weight:700;'>"
            f"{arrow}{abs(delta):.1f}</td>"
            "</tr>"
        )

    span_note = ""
    if delta_days is not None and delta_days > 1:
        span_note = (
            f"<p style='color:#F59E0B;font-size:11px;margin:0 0 6px;'>"
            f"⚠ Last digest was {delta_days} days ago — delta spans "
            f"that gap, not a single day.</p>"
        )

    return (
        "<div style='margin:14px 0 6px;'>"
        "<p style='color:#0F172A;font-size:14px;margin:0 0 4px;font-weight:600;'>"
        "What changed:</p>"
        f"{span_note}"
        "<table cellpadding='0' cellspacing='0' border='0' "
        "style='font-size:12px;margin:0 0 10px;'>"
        f"{''.join(rows_html)}"
        "</table>"
        "</div>"
    )


def _format_body_html(
    score: float,
    delta: float | None,
    stats: dict,
    unlock: dict,
    raw_view: dict | None = None,
    calibration: dict | None = None,
    movers: list[dict] | None = None,
    delta_days: int | None = None,
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

    movers_block = _format_gate_movers_block(movers or [], delta_days)
    calendar_block = _format_calendar_context(stats)

    return f"""
<h2 style="color:#0F172A;font-size:20px;margin:0 0 8px;font-weight:800;">
  Tier 3 readiness: <span style="color:#0052FF">{score:.1f} / 100</span>
</h2>
<p style="color:#64748B;font-size:13px;margin:0 0 6px;">{delta_str}</p>
{calibration_badge}
{unlocked_badge}
{movers_block}
<p style="color:#0F172A;font-size:14px;margin:14px 0 4px;font-weight:600;">
  Blockers ({len(reasons)}):
</p>
<p style="color:#334155;font-size:13px;margin:0 0 14px;">{chip_line}</p>
{calendar_block}
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
        delta_days: int | None = None
        movers: list[dict] = []
        if prior and isinstance(prior.get("score"), (int, float)):
            delta = round(score - float(prior["score"]), 2)
            try:
                prior_date = datetime.strptime(prior["date"], "%Y-%m-%d").date()
                today_date = datetime.strptime(today, "%Y-%m-%d").date()
                delta_days = (today_date - prior_date).days
            except (KeyError, ValueError, TypeError):
                delta_days = None
            movers = _compute_gate_movers(
                current_stats=stats, prior_stats=prior.get("stats"),
            )

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
                movers=movers,
                delta_days=delta_days,
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
