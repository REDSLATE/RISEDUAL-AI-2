"""Daily ML-health digest — admin-only morning brief.

Emails a compact ML safety-rail health snapshot to the owner every
morning at 08:00 UTC. Closes the "I forgot to check the admin
dashboard for 3 days" loop: the email surfaces shadow/soften/
revert counts from the previous 24h, the top toxic metric
triggers, the current active-adaptation roster, and the latest
tuning recommendation from the calibration endpoint.

Design rules (mirrors ``tier3_readiness_digest``)
-------------------------------------------------
* Fire-and-forget: any failure logs and returns — never raises.
* Idempotent per day: records the snapshot to
  ``ml_health_digest_history`` so re-runs on the same UTC date
  don't send a duplicate email. Tomorrow's delta is always
  computed against a prior-day snapshot.
* No templating engine — plain HTML wrapped in the existing
  ``_base_html()`` shell.
* Admin-only recipient: defaults to ``OWNER_EMAIL`` /
  ``admin@risedual.ai`` — overridable with
  ``ML_HEALTH_DIGEST_RECIPIENT``.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_HISTORY_COLL = "ml_health_digest_history"
_AUDIT_COLL = "adaptation_audit"
_ADAPT_COLL = "model_adaptations"


def _utc_date_str(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


async def _snapshot_for_today_exists(db: Any, today: str) -> bool:
    try:
        found = await db[_HISTORY_COLL].find_one(
            {"date": today}, projection={"_id": 1},
        )
        return found is not None
    except Exception as exc:
        logger.warning("[ml-health-digest] today lookup failed: %s", exc)
        return False


async def _record_snapshot(db: Any, today: str, summary: dict) -> None:
    try:
        await db[_HISTORY_COLL].update_one(
            {"date": today},
            {"$set": {
                "date": today,
                "summary": summary,
                "recorded_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
    except Exception as exc:
        logger.warning("[ml-health-digest] record failed: %s", exc)


async def collect_ml_health_data(db: Any, window_hours: int = 24) -> dict:
    """Gather the day's safety-rail activity + current state.

    Returns a dict with:
      * ``counts`` — ``{auto_soften, auto_revert, shadow_soften,
        shadow_revert, total}`` over the last ``window_hours``.
      * ``top_metrics`` — top-5 (metric, n) pairs from the audit.
      * ``active_adaptations`` — count of currently-active rules
        + a short list of the 3 most recently created.
      * ``config`` — output of ``get_auto_revert_config`` (so the
        email documents which thresholds the scanner ran with).
      * ``recommendation`` — latest p25-based calibration hint, if
        any (re-uses the calibration endpoint's logic).
      * ``modes`` — ``{live, shadow}`` flags so the email shows
        whether the rail is observing vs acting.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    counts = {
        "auto_soften": 0, "auto_revert": 0,
        "shadow_soften": 0, "shadow_revert": 0,
        "total": 0,
    }
    top_metrics: dict[str, int] = {}
    scores: list[float] = []
    try:
        cursor = db[_AUDIT_COLL].find(
            {
                "action": {"$in": list(counts.keys() - {"total"})},
                "at": {"$gte": since},
            },
            {"_id": 0, "action": 1, "metric": 1, "decision_score": 1},
        )
        async for row in cursor:
            action = row.get("action")
            if action in counts:
                counts[action] += 1
                counts["total"] += 1
            metric = row.get("metric") or "unknown"
            top_metrics[metric] = top_metrics.get(metric, 0) + 1
            ds = row.get("decision_score")
            if ds is not None:
                try:
                    scores.append(float(ds))
                except (TypeError, ValueError):
                    pass
    except Exception as exc:
        logger.warning("[ml-health-digest] audit scan failed: %s", exc)

    top_sorted = sorted(top_metrics.items(), key=lambda kv: kv[1], reverse=True)[:5]

    # Active adaptations roster
    active_count = 0
    recent_active: list[dict] = []
    try:
        active_count = await db[_ADAPT_COLL].count_documents({"active": True})
        cursor = (
            db[_ADAPT_COLL]
            .find({"active": True}, {
                "_id": 0, "metric": 1, "direction": 1,
                "adjustment_factor": 1, "auto_softening_steps": 1,
                "created_at": 1,
            })
            .sort("created_at", -1)
            .limit(3)
        )
        async for row in cursor:
            recent_active.append(row)
    except Exception as exc:
        logger.warning("[ml-health-digest] active roster failed: %s", exc)

    # Config snapshot + mode flags + threshold recommendation
    try:
        from services.model_adaptation import (
            get_auto_revert_config,
            auto_revert_enabled,
            auto_revert_shadow_mode,
        )
        cfg = get_auto_revert_config()
        modes = {
            "live": auto_revert_enabled(),
            "shadow": auto_revert_shadow_mode(),
        }
    except Exception as exc:
        logger.warning("[ml-health-digest] config fetch failed: %s", exc)
        cfg = {}
        modes = {"live": False, "shadow": False}

    # Tuning recommendation (p25 of observed scores, 30-day lookback)
    recommendation: dict | None = None
    try:
        # Pull a wider window for recommendation stability (30 days).
        rec_since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        rec_scores: list[float] = []
        async for row in db[_AUDIT_COLL].find(
            {
                "action": {"$in": ["shadow_soften", "shadow_revert",
                                   "auto_soften", "auto_revert"]},
                "at": {"$gte": rec_since},
            },
            {"_id": 0, "decision_score": 1},
        ):
            ds = row.get("decision_score")
            if ds is not None:
                try:
                    rec_scores.append(float(ds))
                except (TypeError, ValueError):
                    pass
        if len(rec_scores) >= 20:
            rec_scores.sort()
            idx = max(0, min(len(rec_scores) - 1,
                             int(round((len(rec_scores) - 1) * 0.25))))
            p25 = max(rec_scores[idx], 0.0001)
            current = float(cfg.get("effect_size") or 0.0)
            if current > 0 and abs(p25 - current) / current > 0.05:
                recommendation = {
                    "suggested_effect_size": round(p25, 6),
                    "current_effect_size": current,
                    "direction": "tighten" if p25 > current else "loosen",
                    "observations": len(rec_scores),
                }
    except Exception as exc:
        logger.warning("[ml-health-digest] recommendation failed: %s", exc)

    return {
        "window_hours": window_hours,
        "counts": counts,
        "top_metrics": top_sorted,
        "active_count": active_count,
        "recent_active": recent_active,
        "config": cfg,
        "modes": modes,
        "recommendation": recommendation,
        "day_scores_count": len(scores),
    }


def _format_subject(data: dict) -> str:
    c = data.get("counts") or {}
    total = int(c.get("total") or 0)
    modes = data.get("modes") or {}
    mode_tag = "LIVE" if modes.get("live") else ("SHADOW" if modes.get("shadow") else "OFF")
    if total == 0:
        return f"[RISEDUAL] ML health — quiet ({mode_tag})"
    return f"[RISEDUAL] ML health — {total} action{'' if total == 1 else 's'} ({mode_tag})"


def _format_body_html(data: dict) -> str:
    c = data.get("counts") or {}
    cfg = data.get("config") or {}
    modes = data.get("modes") or {}
    top = data.get("top_metrics") or []
    recent = data.get("recent_active") or []
    active_count = int(data.get("active_count") or 0)
    window_hrs = int(data.get("window_hours") or 24)
    rec = data.get("recommendation")

    # ── Mode banner ──
    if modes.get("live"):
        mode_html = ("<div style='display:inline-block;background:#DCFCE7;color:#166534;"
                     "font-size:11px;font-weight:800;padding:4px 10px;border-radius:6px;"
                     "letter-spacing:0.5px;'>LIVE</div>")
    elif modes.get("shadow"):
        mode_html = ("<div style='display:inline-block;background:#E0E7FF;color:#3730A3;"
                     "font-size:11px;font-weight:800;padding:4px 10px;border-radius:6px;"
                     "letter-spacing:0.5px;'>SHADOW</div>")
    else:
        mode_html = ("<div style='display:inline-block;background:#F1F5F9;color:#64748B;"
                     "font-size:11px;font-weight:800;padding:4px 10px;border-radius:6px;"
                     "letter-spacing:0.5px;'>OFF</div>")

    # ── Counts tiles ──
    tile = (
        "<td align='center' style='padding:12px 8px;vertical-align:top;'>"
        "<p style='color:#64748B;font-size:10px;margin:0 0 4px;text-transform:uppercase;"
        "letter-spacing:1px;font-weight:700;'>{label}</p>"
        "<p style='color:{color};font-size:22px;margin:0;font-weight:800;'>{value}</p>"
        "</td>"
    )
    counts_html = (
        "<table width='100%' cellpadding='0' cellspacing='0' border='0' bgcolor='#F8FAFC' "
        "style='background:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin:16px 0;'>"
        "<tr>"
        + tile.format(label="Auto-soften", color="#7C3AED", value=int(c.get("auto_soften") or 0))
        + tile.format(label="Auto-revert", color="#DC2626", value=int(c.get("auto_revert") or 0))
        + tile.format(label="Shadow soften", color="#4F46E5", value=int(c.get("shadow_soften") or 0))
        + tile.format(label="Shadow revert", color="#0891B2", value=int(c.get("shadow_revert") or 0))
        + "</tr></table>"
    )

    # ── Top metrics ──
    if top:
        rows = "".join(
            f"<tr><td style='padding:6px 12px;color:#0F172A;font-size:13px;"
            f"border-bottom:1px solid #E2E8F0;font-weight:600;'>{m}</td>"
            f"<td style='padding:6px 12px;color:#475569;font-size:12px;"
            f"border-bottom:1px solid #E2E8F0;text-align:right;'>{n}</td></tr>"
            for m, n in top
        )
        top_html = (
            "<p style='color:#64748B;font-size:11px;margin:16px 0 6px;text-transform:uppercase;"
            "letter-spacing:1.5px;font-weight:700;'>Top metrics triggering</p>"
            "<table width='100%' cellpadding='0' cellspacing='0' border='0' bgcolor='#FFFFFF' "
            "style='background:#FFFFFF;border-radius:10px;border:1px solid #E2E8F0;"
            "border-collapse:collapse;'>" + rows + "</table>"
        )
    else:
        top_html = (
            "<p style='color:#64748B;font-size:12px;margin:16px 0 0;font-style:italic;'>"
            "No safety-rail actions in the last "
            f"{window_hrs}h — scanner is quiet.</p>"
        )

    # ── Active adaptations roster ──
    roster_html = ""
    if recent:
        items = "".join(
            f"<li style='color:#0F172A;font-size:12px;margin:2px 0;'>"
            f"<span style='font-family:monospace;'>{r.get('metric')}/{r.get('direction','ANY')}</span> "
            f"<span style='color:#64748B;'>factor={r.get('adjustment_factor')}</span>"
            + (f" <span style='color:#7C3AED;'>· softened ×{r.get('auto_softening_steps')}</span>"
               if r.get("auto_softening_steps") else "")
            + "</li>"
            for r in recent
        )
        roster_html = (
            f"<p style='color:#64748B;font-size:11px;margin:16px 0 6px;text-transform:uppercase;"
            f"letter-spacing:1.5px;font-weight:700;'>Active rules ({active_count} total)</p>"
            f"<ul style='margin:0;padding-left:18px;'>{items}</ul>"
        )
    elif active_count == 0:
        roster_html = (
            "<p style='color:#64748B;font-size:12px;margin:16px 0 0;font-style:italic;'>"
            "No active adaptations. Retrain will use pristine severity+regime weighting.</p>"
        )

    # ── Tuning recommendation banner ──
    rec_html = ""
    if rec and rec.get("suggested_effect_size") is not None:
        direction = rec.get("direction", "tighten")
        bg = "#FEE2E2" if direction == "tighten" else "#D1FAE5"
        color = "#991B1B" if direction == "tighten" else "#065F46"
        rec_html = (
            f"<table width='100%' cellpadding='0' cellspacing='0' border='0' "
            f"bgcolor='{bg}' style='background:{bg};border-radius:10px;margin:16px 0 0;'>"
            f"<tr><td style='padding:12px 16px;'>"
            f"<p style='color:{color};font-size:11px;margin:0 0 4px;text-transform:uppercase;"
            f"letter-spacing:1.5px;font-weight:800;'>Tuning recommendation · {direction}</p>"
            f"<p style='color:{color};font-size:13px;margin:0;line-height:1.5;'>"
            f"After {rec['observations']} shadow observations, p25 suggests "
            f"<code style='background:#FFFFFF;padding:2px 6px;border-radius:4px;font-family:monospace;'>"
            f"ML_AUTO_REVERT_EFFECT_SIZE={rec['suggested_effect_size']}</code> "
            f"(current {rec['current_effect_size']}).</p>"
            f"</td></tr></table>"
        )

    # ── Config footer ──
    cfg_html = (
        f"<p style='color:#94A3B8;font-size:10px;margin:16px 0 0;line-height:1.4;font-family:monospace;'>"
        f"effect_size={cfg.get('effect_size','—')} · "
        f"epsilon={cfg.get('epsilon','—')} · "
        f"min_coverage={cfg.get('min_coverage','—')} · "
        f"N={cfg.get('consecutive_negative','—')}"
        f"</p>"
    )

    return (
        "<h2 style='color:#0F172A;font-size:20px;margin:0 0 4px;font-weight:800;'>"
        "ML Safety-Rail Health</h2>"
        f"<p style='color:#64748B;font-size:12px;margin:0 0 10px;'>"
        f"{window_hrs}h activity window · {mode_html}</p>"
        f"{counts_html}"
        f"{top_html}"
        f"{roster_html}"
        f"{rec_html}"
        f"{cfg_html}"
    )


async def run_ml_health_digest(db: Any) -> dict:
    """Scheduled entry point. Never raises — logs and returns a
    summary dict. Admin-only recipient.
    """
    try:
        today = _utc_date_str()
        if await _snapshot_for_today_exists(db, today):
            logger.info("[ml-health-digest] already sent for %s — skipping", today)
            return {"sent": False, "reason": "already_sent_today", "date": today}

        data = await collect_ml_health_data(db, window_hours=24)

        recipient = os.environ.get(
            "ML_HEALTH_DIGEST_RECIPIENT",
            os.environ.get("OWNER_EMAIL", "admin@risedual.ai"),
        )
        subject = _format_subject(data)

        from services.email_service import _base_html, _is_configured, _routed_send
        if not _is_configured():
            logger.info("[ml-health-digest] skipped — no email provider configured")
            await _record_snapshot(db, today, {
                "sent": False, "reason": "no_provider", "data": data,
            })
            return {"sent": False, "reason": "no_email_provider", "date": today}

        counts = data.get("counts") or {}
        preheader = (
            f"soften={counts.get('auto_soften',0)+counts.get('shadow_soften',0)} · "
            f"revert={counts.get('auto_revert',0)+counts.get('shadow_revert',0)} · "
            f"active={data.get('active_count',0)}"
        )
        html = _base_html(_format_body_html(data), preheader=preheader)
        sent = await _routed_send([recipient], subject, html)

        await _record_snapshot(db, today, {
            "sent": bool(sent),
            "recipient": recipient,
            "subject": subject,
            "counts": counts,
            "active_count": data.get("active_count"),
            "modes": data.get("modes"),
        })

        logger.info(
            "[ml-health-digest] sent=%s recipient=%s counts=%s",
            sent, recipient, counts,
        )
        return {
            "sent": bool(sent),
            "date": today,
            "recipient": recipient,
            "counts": counts,
            "active_count": data.get("active_count"),
            "has_recommendation": data.get("recommendation") is not None,
        }
    except Exception as exc:
        logger.exception("[ml-health-digest] run failed: %s", exc)
        return {"sent": False, "error": str(exc)}
