"""Daily digest email service — collects real market data and sends a morning briefing.

Data sources (as of Feb 2026 schema):
- Top AI Predictions      : ``predictions`` collection, last 48h, ranked by confidence.
- Smart Money (Dark Pool) : ``smart_money_scores`` collection, latest per symbol, strongest signals.
- Market Alerts           : ``sec_13f_alerts`` collection, largest absolute score deltas.
- Market Overview         : ``prediction_cache`` scope=``market_overview`` (top-level AI narrative).
- Watchlist Intelligence  : ``watchlist_intelligence`` cache per user.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from services.email_service import (
    APP_NAME,
    APP_URL,
    _base_html,
    _is_configured,
    _routed_send,
)

logger = logging.getLogger(__name__)

# Resend free plan = 5 req/sec; pace sends to stay comfortably under the limit.
_DIGEST_SEND_PACE_SEC = 0.25


# ─────────────────────────── Data collection ───────────────────────────

async def collect_digest_data(db) -> dict:
    """Aggregate market data needed for the daily digest from live collections."""
    now = datetime.now(timezone.utc)
    since_48h = (now - timedelta(hours=48)).isoformat()
    since_7d = (now - timedelta(days=7)).isoformat()

    data: dict = {
        "overview": None,
        "predictions": [],
        "smart_money": [],
        "alerts": [],
        "timestamp": now.isoformat(),
    }

    # ── Market-wide overview narrative (AI) ──
    try:
        cache_doc = await db.prediction_cache.find_one(
            {"scope": "market_overview", "status": "ready"},
            {"_id": 0},
            sort=[("updatedAt", -1)],
        )
        if cache_doc and isinstance(cache_doc.get("prediction"), dict):
            p = cache_doc["prediction"]
            data["overview"] = {
                "headline": (p.get("headline") or p.get("verdict") or p.get("summary") or "").strip(),
                "outlook": (p.get("outlook") or p.get("body") or p.get("analysis") or "").strip(),
                "regime": p.get("regime") or p.get("risk_regime") or "",
                "updated_at": cache_doc.get("updatedAt"),
            }
    except Exception as e:
        logger.warning(f"Digest: error fetching market overview: {e}")

    # ── Top AI Predictions (last 48h, by confidence) ──
    try:
        cursor = db.predictions.find(
            {"timestamp": {"$gte": since_48h}},
            {"_id": 0, "symbol": 1, "direction": 1, "confidence": 1, "score": 1,
             "price_at_prediction": 1, "feature": 1, "timestamp": 1},
        ).sort([("confidence", -1), ("timestamp", -1)]).limit(25)

        seen: set[str] = set()
        async for doc in cursor:
            sym = (doc.get("symbol") or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            data["predictions"].append({
                "symbol": sym,
                "direction": (doc.get("direction") or "NEUTRAL").upper(),
                "confidence": int(doc.get("confidence") or 0),
                "score": int(doc.get("score") or 0),
                "price": float(doc.get("price_at_prediction") or 0) or None,
                "feature": doc.get("feature") or "prediction",
            })
            if len(data["predictions"]) >= 5:
                break
    except Exception as e:
        logger.warning(f"Digest: error fetching predictions: {e}")

    # ── Smart Money Scores (latest per symbol, strongest signals) ──
    try:
        pipeline = [
            {"$sort": {"snapshot_at": -1}},
            {"$group": {
                "_id": "$symbol",
                "latest": {"$first": "$$ROOT"},
            }},
            {"$replaceRoot": {"newRoot": "$latest"}},
            {"$addFields": {"abs_dev": {"$abs": {"$subtract": ["$score", 50]}}}},
            {"$sort": {"abs_dev": -1}},
            {"$limit": 6},
        ]
        async for doc in db.smart_money_scores.aggregate(pipeline):
            data["smart_money"].append({
                "symbol": doc.get("symbol", ""),
                "score": int(doc.get("score") or 0),
                "signal": doc.get("signal", "neutral"),
                "bullish": int(doc.get("bullish_count") or 0),
                "bearish": int(doc.get("bearish_count") or 0),
                "holders": int(doc.get("holder_count") or 0),
                "net_flow_usd": float(doc.get("net_flow_usd") or 0),
            })
    except Exception as e:
        logger.warning(f"Digest: error fetching smart money scores: {e}")

    # ── Alerts — largest abs delta in last 7 days ──
    try:
        cursor = db.sec_13f_alerts.find(
            {"created_at": {"$gte": since_7d}},
            {"_id": 0},
        ).sort("created_at", -1).limit(50)
        alerts: list[dict] = []
        async for doc in cursor:
            delta = int(doc.get("delta") or 0)
            alerts.append({
                "symbol": doc.get("symbol", ""),
                "type": doc.get("type", "smart_money_shift"),
                "delta": delta,
                "abs_delta": abs(delta),
                "signal_change": doc.get("signal_change", ""),
                "prev_score": int(doc.get("prev_score") or 0),
                "new_score": int(doc.get("new_score") or 0),
            })
        alerts.sort(key=lambda a: a["abs_delta"], reverse=True)
        data["alerts"] = alerts[:5]
    except Exception as e:
        logger.warning(f"Digest: error fetching alerts: {e}")

    return data


async def get_user_watchlist_intel(db, user_id) -> dict | None:
    """Return cached watchlist intelligence for a user if available."""
    try:
        cached = await db.watchlist_intelligence.find_one(
            {"cache_key": f"wl_intel_{user_id}"}, {"_id": 0},
        )
        if cached and cached.get("data"):
            return cached["data"]
    except Exception as e:
        logger.warning(f"Digest: error fetching watchlist intel for {user_id}: {e}")
    return None


# ─────────────────────────── HTML rendering ───────────────────────────

def _verdict_color(v: str) -> str:
    v = (v or "").upper()
    if "BULL" in v or "BUY" in v:
        return "#059669"  # green
    if "BEAR" in v or "SELL" in v:
        return "#DC2626"  # red
    return "#D97706"  # amber


def _signal_color(s: str) -> str:
    s = (s or "").lower()
    if s == "bullish":
        return "#059669"
    if s == "bearish":
        return "#DC2626"
    return "#D97706"


def _score_color_light(score: int) -> str:
    if score >= 70:
        return "#059669"
    if score >= 40:
        return "#D97706"
    return "#DC2626"


def _section_label(label: str, accent: str = "#0052FF") -> str:
    return (
        f'<p style="color:{accent};font-size:11px;margin:0 0 8px;text-transform:uppercase;'
        f'letter-spacing:1.5px;font-weight:700;">{label}</p>'
    )


def _blurred_row_cols(cols: int = 2) -> str:
    """Render a teaser blur row for free tier."""
    cells = "".join(
        f'<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:#CBD5E1;'
        f'font-size:12px;text-align:{"right" if i else "left"};">'
        f'<span style="filter:blur(4px);">████████</span></td>'
        for i in range(cols)
    )
    return f"<tr>{cells}</tr>"


def _format_usd(value: float) -> str:
    v = abs(value)
    if v >= 1_000_000_000:
        return f"${value/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${value/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${value/1_000:.0f}K"
    return f"${value:.0f}"


def _overview_block(overview: dict | None) -> str:
    if not overview:
        return ""
    headline = (overview.get("headline") or "").strip()
    outlook = (overview.get("outlook") or "").strip()
    regime = (overview.get("regime") or "").strip()
    if not (headline or outlook):
        return ""
    regime_pill = ""
    if regime:
        regime_pill = (
            f'<span style="display:inline-block;background-color:#EFF6FF;color:#0052FF;'
            f'font-size:10px;font-weight:700;padding:3px 8px;border-radius:6px;'
            f'text-transform:uppercase;letter-spacing:1px;">{regime}</span>'
        )
    return f"""
{_section_label("AI Market Overview")}
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#EFF6FF" style="background-color:#EFF6FF;border-radius:10px;border:1px solid #BFDBFE;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
{regime_pill}
<p style="color:#0F172A;font-size:15px;margin:{"8px 0 6px" if regime_pill else "0 0 6px"};font-weight:700;line-height:1.5;">{headline}</p>
<p style="color:#475569;font-size:13px;margin:0;line-height:1.6;">{outlook}</p>
</td></tr>
</table>
"""


def _predictions_block(predictions: list[dict], is_pro: bool) -> str:
    if not predictions:
        return (
            _section_label("AI Market Predictions") +
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F8FAFC" '
            'style="background-color:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:16px;">'
            '<tr><td style="padding:14px;color:#64748B;font-size:12px;text-align:center;">'
            'No high-conviction predictions in the last 48h.</td></tr></table>'
        )

    rows = ""
    for i, p in enumerate(predictions):
        if not is_pro and i >= 2:
            rows += _blurred_row_cols(3)
            continue
        dir_color = _verdict_color(p["direction"])
        price = f' · ${p["price"]:.2f}' if p.get("price") else ""
        rows += f"""<tr>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:#0F172A;font-size:13px;font-weight:700;">{p['symbol']}</td>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:{dir_color};font-size:12px;font-weight:700;text-align:center;">{p['direction']}</td>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:#475569;font-size:12px;text-align:right;">{p['confidence']}% conf{price}</td>
</tr>"""

    return f"""
{_section_label("Top AI Predictions (48h)")}
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF" style="background-color:#FFFFFF;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:16px;">
{rows}
</table>
"""


def _smart_money_block(smart_money: list[dict], is_pro: bool) -> str:
    if not smart_money:
        return (
            _section_label("Smart Money Flow", "#7C3AED") +
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F8FAFC" '
            'style="background-color:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:16px;">'
            '<tr><td style="padding:14px;color:#64748B;font-size:12px;text-align:center;">'
            'No smart money signals available yet.</td></tr></table>'
        )

    rows = ""
    for i, sm in enumerate(smart_money):
        if not is_pro and i >= 2:
            rows += _blurred_row_cols(3)
            continue
        sig_color = _signal_color(sm["signal"])
        score_color = _score_color_light(sm["score"])
        flow_txt = _format_usd(sm["net_flow_usd"]) if sm.get("net_flow_usd") else f'{sm["bullish"]}↑ / {sm["bearish"]}↓'
        rows += f"""<tr>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:#0F172A;font-size:13px;font-weight:700;">{sm['symbol']}</td>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;font-size:12px;text-align:center;">
<span style="display:inline-block;background-color:{score_color}20;color:{score_color};font-size:11px;font-weight:700;padding:3px 8px;border-radius:6px;">{sm['score']}/100</span>
</td>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:{sig_color};font-size:12px;font-weight:600;text-align:right;">{sm['signal'].title()} · {flow_txt}</td>
</tr>"""

    return f"""
{_section_label("Smart Money Flow (Institutional)", "#7C3AED")}
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF" style="background-color:#FFFFFF;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:16px;">
{rows}
</table>
"""


def _alerts_block(alerts: list[dict], is_pro: bool) -> str:
    if not alerts:
        return ""
    rows = ""
    for i, a in enumerate(alerts):
        if not is_pro and i >= 1:
            rows += _blurred_row_cols(3)
            continue
        d = a["delta"]
        color = "#059669" if d > 0 else "#DC2626" if d < 0 else "#64748B"
        sign = "+" if d > 0 else ""
        shift = (a.get("signal_change") or "").replace("→", "&rarr;") or "shift"
        rows += f"""<tr>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:#0F172A;font-size:13px;font-weight:700;">{a['symbol']}</td>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:{color};font-size:12px;font-weight:700;text-align:center;">{sign}{d}</td>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:#475569;font-size:11px;text-align:right;">{shift}</td>
</tr>"""
    return f"""
{_section_label("Market Alerts (7d)", "#EA580C")}
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF" style="background-color:#FFFFFF;border-radius:10px;border:1px solid #FED7AA;margin-bottom:16px;">
{rows}
</table>
"""


# ── Watchlist Intelligence section ──

def _wl_health_header(summary: dict) -> str:
    health = int(summary.get("health_score", 0) or 0)
    color = _score_color_light(health)
    headline = (summary.get("headline") or "Your Watchlist").strip()
    outlook = (summary.get("outlook") or "").strip()
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F5F3FF" style="background-color:#F5F3FF;border-radius:10px;border:1px solid #DDD6FE;margin-bottom:16px;">
<tr><td style="padding:16px 20px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
<td style="vertical-align:top;">
<p style="color:#7C3AED;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1.5px;font-weight:700;">Your Watchlist Intelligence</p>
<p style="color:#0F172A;font-size:16px;margin:0;font-weight:700;">{headline}</p>
</td>
<td style="text-align:right;vertical-align:top;width:80px;">
<span style="display:inline-block;background-color:{color};color:#FFFFFF;border-radius:8px;padding:6px 10px;font-size:13px;font-weight:800;">{health}</span>
<p style="color:#64748B;font-size:9px;margin:4px 0 0;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Health</p>
</td>
</tr>
</table>
{f'<p style="color:#475569;font-size:12px;margin:10px 0 0;line-height:1.5;">{outlook}</p>' if outlook else ''}
</td></tr>
</table>
"""


def _wl_ticker_grid(tickers: list[dict], is_pro: bool) -> str:
    if not tickers:
        return ""
    rows = ""
    for i, t in enumerate(tickers[:6]):
        if not is_pro and i >= 2:
            rows += _blurred_row_cols(2)
            continue
        score = int(t.get("score", 0) or 0)
        verdict = (t.get("verdict") or "hold").upper()
        one_liner = (t.get("one_liner") or "").strip()
        score_color = _score_color_light(int(score * 10) if score <= 10 else score)
        vcolor = _verdict_color(verdict)
        rows += f"""<tr>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;">
<span style="color:#0F172A;font-size:13px;font-weight:700;">{t.get('symbol','')}</span>
<span style="color:{score_color};font-size:12px;font-weight:800;margin-left:8px;">{score}{'/10' if score <= 10 else ''}</span>
<span style="display:inline-block;background-color:{vcolor}20;color:{vcolor};font-size:9px;font-weight:800;padding:2px 6px;border-radius:4px;margin-left:6px;">{verdict}</span>
</td>
<td style="padding:10px 12px;border-bottom:1px solid #E2E8F0;color:#475569;font-size:11px;text-align:right;">{one_liner}</td>
</tr>"""
    return f"""
{_section_label("Ticker Scores", "#7C3AED")}
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF" style="background-color:#FFFFFF;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:16px;">
{rows}
</table>
"""


def _wl_top_movers(movers: list[dict]) -> str:
    if not movers:
        return ""
    pills = ""
    for m in movers[:4]:
        pct = float(m.get("change_pct", 0) or 0)
        color = "#059669" if pct >= 0 else "#DC2626"
        sign = "+" if pct >= 0 else ""
        pills += (
            f'<td style="padding:6px 10px;"><span style="color:#0F172A;font-size:12px;'
            f'font-weight:700;">{m.get("symbol","")}</span> '
            f'<span style="color:{color};font-size:12px;font-weight:800;">{sign}{pct:.1f}%</span></td>'
        )
    return f"""
{_section_label("Top Movers", "#06B6D4")}
<table cellpadding="0" cellspacing="0" border="0" bgcolor="#F8FAFC" style="background-color:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:20px;">
<tr>{pills}</tr>
</table>
"""


def _watchlist_section(wl_data: dict | None, is_pro: bool) -> str:
    if not wl_data:
        return ""
    tickers = wl_data.get("tickers") or []
    if not tickers:
        return ""
    summary = wl_data.get("summary") or {}
    top_movers = wl_data.get("top_movers") or []
    return (
        _wl_health_header(summary)
        + _wl_ticker_grid(tickers, is_pro)
        + _wl_top_movers(top_movers)
    )


def _upgrade_cta() -> str:
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#EFF6FF" style="background-color:#EFF6FF;border-radius:10px;border:1px solid #BFDBFE;margin:20px 0;">
<tr><td align="center" style="padding:18px;">
<p style="color:#0052FF;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1.5px;font-weight:700;">Unlock the Full Digest</p>
<p style="color:#475569;font-size:13px;margin:0 0 12px;">Pro reveals all predictions, smart money flows, and watchlist signals.</p>
<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
<tr><td bgcolor="#0052FF" style="background-color:#0052FF;border-radius:8px;">
<a href="{APP_URL}" style="display:inline-block;color:#FFFFFF;text-decoration:none;font-size:13px;font-weight:600;padding:10px 24px;">Upgrade to Pro &mdash; $45/mo</a>
</td></tr>
</table>
</td></tr>
</table>"""


def build_digest_html(data: dict, is_pro: bool, user_name: str,
                      watchlist_intel: dict | None = None) -> str:
    """Render the digest HTML email. Pro users see full data; free users see a teaser."""
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    name = (user_name or "Trader").strip() or "Trader"

    wl_section = _watchlist_section(watchlist_intel, is_pro)
    overview = _overview_block(data.get("overview"))
    predictions = _predictions_block(data.get("predictions") or [], is_pro)
    smart_money = _smart_money_block(data.get("smart_money") or [], is_pro)
    alerts = _alerts_block(data.get("alerts") or [], is_pro)
    upgrade = "" if is_pro else _upgrade_cta()

    content = f"""
<h2 style="color:#0F172A;font-size:22px;margin:0 0 4px;font-weight:800;">Good morning, {name}</h2>
<p style="color:#64748B;font-size:12px;margin:0 0 20px;font-weight:600;">{date_str} &mdash; Daily Market Digest</p>
{wl_section}
{overview}
{predictions}
{smart_money}
{alerts}
{upgrade}
<table cellpadding="0" cellspacing="0" border="0" style="margin:20px auto 0;">
<tr><td bgcolor="#0052FF" style="background-color:#0052FF;border-radius:10px;">
<a href="{APP_URL}" style="display:inline-block;color:#FFFFFF;text-decoration:none;font-size:14px;font-weight:600;padding:12px 28px;">Open {APP_NAME}</a>
</td></tr>
</table>"""

    # Build a useful preheader reflecting what's in the email
    preheader_parts: list[Any] = []
    if data.get("predictions"):
        p = data["predictions"][0]
        preheader_parts.append(f"{p['symbol']} {p['direction']} {p['confidence']}%")
    if data.get("smart_money"):
        s = data["smart_money"][0]
        preheader_parts.append(f"Smart Money: {s['symbol']} {s['signal']}")
    if data.get("alerts"):
        a = data["alerts"][0]
        preheader_parts.append(f"Alert: {a['symbol']} {'+' if a['delta']>0 else ''}{a['delta']}")
    preheader = " · ".join(preheader_parts) or f"Your {APP_NAME} morning briefing is ready."

    return _base_html(content, preheader=preheader)


# ─────────────────────────── Send ───────────────────────────

async def send_daily_digest(db) -> dict:
    """Collect data, render per-user digest, send via Resend (→ SendGrid failover)."""
    if not _is_configured():
        logger.info("Daily digest skipped: no email providers configured")
        return {"sent": 0, "skipped": True, "reason": "no_email_provider"}

    data = await collect_digest_data(db)
    logger.info(
        "Digest data collected: "
        f"{len(data['predictions'])} predictions, "
        f"{len(data['smart_money'])} smart_money, "
        f"{len(data['alerts'])} alerts, "
        f"overview={'yes' if data.get('overview') else 'no'}"
    )

    # If we truly have no data, skip rather than send an empty digest.
    has_content = bool(
        data.get("overview")
        or data.get("predictions")
        or data.get("smart_money")
        or data.get("alerts")
    )
    if not has_content:
        logger.warning("Daily digest skipped: no market content available (predictions/smart_money/alerts/overview all empty)")
        return {"sent": 0, "skipped": True, "reason": "no_content"}

    sent_count = 0
    error_count = 0
    wl_count = 0
    skipped_count = 0

    cursor = db.users.find(
        {"digest_opt_out": {"$ne": True}, "is_active": {"$ne": False}},
        {"email": 1, "name": 1, "subscription_status": 1},
    )

    async for user in cursor:
        email = (user.get("email") or "").strip().lower()
        if not email or "@" not in email:
            skipped_count += 1
            continue
        # Skip seeded/test/example accounts — they don't receive real mail
        # and just consume Resend quota.
        domain = email.split("@", 1)[1]
        if domain in {"test.com", "example.com", "example.org", "test.local"} or email.startswith("test_") or email.startswith("emailtest") or email.startswith("emailfix"):
            skipped_count += 1
            continue

        name = user.get("name") or email.split("@")[0]
        is_pro = user.get("subscription_status") in ("pro", "pro_max", "trial")

        wl_intel = await get_user_watchlist_intel(db, user.get("_id"))
        if wl_intel and wl_intel.get("tickers"):
            wl_count += 1

        html = build_digest_html(data, is_pro, name, watchlist_intel=wl_intel)
        subject = f"Your Morning Market Briefing — {datetime.now(timezone.utc).strftime('%b %d')}"

        try:
            ok = await _routed_send([email], subject, html)
            if ok:
                sent_count += 1
                logger.info(f"Digest sent to {email} (pro={is_pro}, wl={'yes' if wl_intel else 'no'})")
            else:
                error_count += 1
        except Exception as e:
            error_count += 1
            logger.error(f"Digest send failed for {email}: {e}")

        # Pace to respect Resend's 5-req/sec rate limit.
        await asyncio.sleep(_DIGEST_SEND_PACE_SEC)

    logger.info(
        f"Daily digest complete: {sent_count} sent, {error_count} errors, "
        f"{wl_count} with watchlist intel, {skipped_count} skipped"
    )
    return {
        "sent": sent_count,
        "errors": error_count,
        "skipped": False,
        "with_watchlist": wl_count,
        "content_summary": {
            "predictions": len(data["predictions"]),
            "smart_money": len(data["smart_money"]),
            "alerts": len(data["alerts"]),
            "overview": bool(data.get("overview")),
        },
    }


async def send_digest_to_user(db, user: dict) -> dict:
    """Build and send a single on-demand digest to the supplied user.

    Used by `POST /api/digest/send-now` — separate from the scheduled
    `send_daily_digest` because it must: (a) bypass the opt-out check
    (user explicitly asked for it), (b) not pace the outbound queue,
    (c) return a payload the UI can show inline.
    """
    if not _is_configured():
        return {"sent": False, "reason": "no_email_provider"}

    email = (user.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return {"sent": False, "reason": "invalid_email"}

    data = await collect_digest_data(db)
    if not (data.get("overview") or data.get("predictions") or data.get("smart_money") or data.get("alerts")):
        return {"sent": False, "reason": "no_content"}

    name = user.get("name") or email.split("@")[0]
    is_pro = user.get("subscription_status") in ("pro", "pro_max", "trial")
    wl_intel = await get_user_watchlist_intel(db, user.get("_id"))
    html = build_digest_html(data, is_pro, name, watchlist_intel=wl_intel)
    subject = f"Your On-Demand Market Briefing — {datetime.now(timezone.utc).strftime('%b %d, %H:%M UTC')}"

    try:
        ok = await _routed_send([email], subject, html)
    except Exception as e:
        logger.error(f"On-demand digest send failed for {email}: {e}")
        return {"sent": False, "reason": "send_error", "detail": str(e)}

    return {
        "sent": bool(ok),
        "email": email,
        "has_watchlist_intel": wl_intel is not None,
        "content_summary": {
            "predictions": len(data["predictions"]),
            "smart_money": len(data["smart_money"]),
            "alerts": len(data["alerts"]),
            "overview": bool(data.get("overview")),
        },
    }
