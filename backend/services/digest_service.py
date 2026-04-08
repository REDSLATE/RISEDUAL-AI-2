"""Daily digest email service - collects market data and sends morning briefing."""
import logging
import asyncio
import resend
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

APP_NAME = "RISEDUAL AI"
APP_URL = "https://risedual.ai"


async def collect_digest_data(db) -> Dict:
    """Collect all data needed for the daily digest."""
    data = {
        "predictions": [],
        "dark_pool": [],
        "signals": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Get latest market predictions from cache/DB
    try:
        cursor = db.market_predictions.find({}).sort("created_at", -1).limit(5)
        async for doc in cursor:
            data["predictions"].append({
                "ticker": doc.get("ticker", "MARKET"),
                "verdict": doc.get("verdict", "N/A"),
                "confidence": doc.get("confidence", 0),
                "summary": doc.get("summary", ""),
            })
    except Exception as e:
        logger.warning(f"Digest: error fetching predictions: {e}")

    # Get latest dark pool data
    try:
        cursor = db.dark_pool_data.find({}).sort("created_at", -1).limit(5)
        async for doc in cursor:
            data["dark_pool"].append({
                "ticker": doc.get("ticker", ""),
                "volume": doc.get("volume", 0),
                "sentiment": doc.get("sentiment", "neutral"),
            })
    except Exception as e:
        logger.warning(f"Digest: error fetching dark pool: {e}")

    # Get latest market signals
    try:
        cursor = db.market_signals.find({}).sort("created_at", -1).limit(5)
        async for doc in cursor:
            data["signals"].append({
                "ticker": doc.get("ticker", ""),
                "signal": doc.get("signal_type", ""),
                "strength": doc.get("strength", ""),
            })
    except Exception as e:
        logger.warning(f"Digest: error fetching signals: {e}")

    return data


async def get_user_watchlist_intel(db, user_id) -> Optional[Dict]:
    """Get cached watchlist intelligence for a user, if available."""
    try:
        cache_key = f"wl_intel_{user_id}"
        cached = await db.watchlist_intelligence.find_one({"cache_key": cache_key}, {"_id": 0})
        if cached and cached.get("data"):
            return cached["data"]
    except Exception as e:
        logger.warning(f"Digest: error fetching watchlist intel for {user_id}: {e}")
    return None


def _row(label: str, value: str, color: str = "#ffffff") -> str:
    return f"""<tr>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:#94A3B8;font-size:12px;">{label}</td>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:{color};font-size:12px;font-weight:600;text-align:right;">{value}</td>
</tr>"""


def _verdict_color(v: str) -> str:
    v = (v or "").upper()
    if "BULL" in v:
        return "#10B981"
    if "BEAR" in v:
        return "#EF4444"
    return "#F59E0B"


def _build_section_rows(items, key_fn, is_pro, max_items=5):
    """Build table rows for a digest section, blurring non-pro items after first."""
    html = ""
    for i, item in enumerate(items[:max_items]):
        if not is_pro and i >= 1:
            html += _blurred_row()
        else:
            html += key_fn(item)
    return html


def _blurred_row():
    return """<tr>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:#475569;font-size:12px;filter:blur(4px);">██████</td>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:#475569;font-size:12px;text-align:right;filter:blur(4px);">████</td>
</tr>"""


def _prediction_row(p):
    return _row(p['ticker'], f"{p['verdict']} ({p['confidence']}%)", _verdict_color(p['verdict']))


def _dark_pool_row(dp):
    sc = "#10B981" if dp["sentiment"] == "bullish" else "#EF4444" if dp["sentiment"] == "bearish" else "#F59E0B"
    return _row(dp["ticker"], dp["sentiment"].title(), sc)


def _signal_row(s):
    return _row(s["ticker"], f"{s['signal']} ({s['strength']})")


def build_digest_html(data: Dict, is_pro: bool, user_name: str, watchlist_intel: Optional[Dict] = None) -> str:
    """Build the digest HTML email. Pro users get full data, free users get teaser."""
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    predictions_html = _build_section_rows(data.get("predictions", [])[:3], _prediction_row, is_pro, 3)
    dark_pool_html = _build_section_rows(data.get("dark_pool", [])[:5], _dark_pool_row, is_pro)
    signals_html = _build_section_rows(data.get("signals", [])[:5], _signal_row, is_pro)

    upgrade_cta = "" if is_pro else _upgrade_cta_html()
    no_data_msg = '<tr><td colspan="2" style="padding:12px;color:#64748B;font-size:12px;text-align:center;">No recent data available</td></tr>'

    # Build watchlist intelligence section
    wl_section = _build_watchlist_section(watchlist_intel, is_pro) if watchlist_intel else ""

    content = f"""
<h2 style="color:#ffffff;font-size:20px;margin:0 0 4px;font-weight:600;">Good Morning, {user_name}</h2>
<p style="color:#64748B;font-size:12px;margin:0 0 20px;">{date_str} — Daily Market Digest</p>

{wl_section}

<!-- AI Predictions -->
<p style="color:#0052FF;font-size:11px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1.5px;font-weight:600;">AI Market Predictions</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:10px;border:1px solid #334155;margin-bottom:16px;">
{predictions_html or no_data_msg}
</table>

<!-- Dark Pool Moves -->
<p style="color:#0052FF;font-size:11px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1.5px;font-weight:600;">Dark Pool Activity</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:10px;border:1px solid #334155;margin-bottom:16px;">
{dark_pool_html or no_data_msg}
</table>

<!-- Market Signals -->
<p style="color:#0052FF;font-size:11px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1.5px;font-weight:600;">Market Signals</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:10px;border:1px solid #334155;margin-bottom:16px;">
{signals_html or no_data_msg}
</table>

{upgrade_cta}

<table cellpadding="0" cellspacing="0" style="margin:16px auto 0;">
<tr><td style="background-color:#0052FF;border-radius:10px;padding:12px 28px;">
<a href="{APP_URL}" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">Open {APP_NAME}</a>
</td></tr>
</table>"""

    return _base_email_html(content)


def _build_watchlist_section(wl_data: Dict, is_pro: bool) -> str:
    """Build the Watchlist Intelligence section for the digest email."""
    summary = wl_data.get("summary", {})
    tickers = wl_data.get("tickers", [])
    alerts = wl_data.get("alerts", [])
    top_movers = wl_data.get("top_movers", [])

    if not tickers:
        return ""

    html = _build_health_header(summary)
    html += _build_alert_rows(alerts)
    html += _build_ticker_grid(tickers, is_pro)
    html += _build_top_movers(top_movers)
    return html


def _score_color(score: int) -> str:
    if score >= 70:
        return "#10B981"
    if score >= 40:
        return "#F59E0B"
    return "#EF4444"


def _build_health_header(summary: Dict) -> str:
    health = summary.get("health_score", 0)
    health_color = _score_color(health)
    return f"""
<!-- Watchlist Intelligence -->
<table width="100%" cellpadding="0" cellspacing="0" style="background:linear-gradient(135deg,#7C3AED20,#4F46E520);border-radius:12px;border:1px solid #7C3AED40;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr>
<td><p style="color:#A78BFA;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1.5px;font-weight:600;">Your Watchlist Intelligence</p>
<p style="color:#ffffff;font-size:16px;margin:0;font-weight:700;">{summary.get('headline', 'Watchlist Summary')}</p></td>
<td style="text-align:right;vertical-align:top;">
<table cellpadding="0" cellspacing="0"><tr>
<td style="background-color:{health_color};border-radius:8px;padding:6px 12px;">
<span style="color:#ffffff;font-size:14px;font-weight:700;">{health}</span>
<span style="color:rgba(255,255,255,0.7);font-size:9px;"> Health</span>
</td></tr></table></td>
</tr>
</table>
<p style="color:#94A3B8;font-size:12px;margin:8px 0 0;line-height:1.5;">{summary.get('outlook', '')}</p>
</td></tr></table>
"""


def _build_alert_rows(alerts: list) -> str:
    high_alerts = [a for a in alerts if a.get("severity") == "high"]
    if not high_alerts:
        return ""

    rows = ""
    for a in high_alerts[:3]:
        rows += f"""<tr>
<td style="padding:8px 12px;border-bottom:1px solid #334155;">
<span style="color:#ffffff;font-size:12px;font-weight:600;">{a.get('symbol','')}</span>
<span style="color:#F59E0B;font-size:10px;"> {a.get('type','').replace('_',' ').upper()}</span>
</td>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:#FCD34D;font-size:11px;">{a.get('message','')}</td>
</tr>"""

    return f"""
<p style="color:#F59E0B;font-size:11px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1.5px;font-weight:600;">Watchlist Alerts</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:10px;border:1px solid #F59E0B30;margin-bottom:16px;">
{rows}
</table>
"""


def _build_ticker_grid(tickers: list, is_pro: bool) -> str:
    ticker_rows = ""
    for i, t in enumerate(tickers[:6]):
        if not is_pro and i >= 2:
            ticker_rows += _blurred_row()
            continue
        score = t.get("score", 0)
        verdict = (t.get("verdict", "hold") or "hold").upper()
        score_color = _score_color(score * 10)  # scale 0-10 to 0-100 for color
        verdict_color = "#10B981" if verdict == "BUY" else "#EF4444" if verdict == "SELL" else "#F59E0B"
        one_liner = t.get("one_liner", "")
        ticker_rows += f"""<tr>
<td style="padding:10px 12px;border-bottom:1px solid #334155;">
<span style="color:#ffffff;font-size:13px;font-weight:700;">{t.get('symbol','')}</span>
<span style="color:{score_color};font-size:13px;font-weight:800;margin-left:8px;">{score}/10</span>
<span style="display:inline-block;background-color:{verdict_color}20;color:{verdict_color};font-size:9px;font-weight:700;padding:2px 6px;border-radius:4px;margin-left:6px;">{verdict}</span>
</td>
<td style="padding:10px 12px;border-bottom:1px solid #334155;color:#94A3B8;font-size:11px;text-align:right;">{one_liner}</td>
</tr>"""

    return f"""
<p style="color:#A78BFA;font-size:11px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1.5px;font-weight:600;">Ticker Scores</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:10px;border:1px solid #334155;margin-bottom:16px;">
{ticker_rows}
</table>
"""


def _build_top_movers(top_movers: list) -> str:
    if not top_movers:
        return ""

    mover_pills = ""
    for m in top_movers[:3]:
        pct = m.get("change_pct", 0)
        mc = "#10B981" if pct >= 0 else "#EF4444"
        sign = "+" if pct >= 0 else ""
        mover_pills += f'<td style="padding:4px 8px;"><span style="color:#ffffff;font-size:12px;font-weight:600;">{m.get("symbol","")}</span> <span style="color:{mc};font-size:12px;font-weight:700;">{sign}{pct}%</span></td>'

    return f"""
<p style="color:#06B6D4;font-size:11px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1.5px;font-weight:600;">Top Movers</p>
<table cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:10px;border:1px solid #334155;margin-bottom:20px;">
<tr>{mover_pills}</tr>
</table>
"""


def _upgrade_cta_html():
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="background:linear-gradient(135deg,#0052FF20,#6366F120);border-radius:12px;border:1px solid #0052FF40;margin:20px 0;">
<tr><td style="padding:16px;text-align:center;">
<p style="color:#0052FF;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Unlock Full Digest</p>
<p style="color:#94A3B8;font-size:13px;margin:0 0 12px;">Upgrade to Pro to see all predictions, dark pool moves, and signals</p>
<a href="{APP_URL}" style="display:inline-block;background-color:#0052FF;color:#ffffff;text-decoration:none;font-size:13px;font-weight:600;padding:10px 24px;border-radius:8px;">Upgrade to Pro — $45/mo</a>
</td></tr>
</table>"""


def _base_email_html(content: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#0F172A;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;padding:40px 20px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background-color:#1E293B;border-radius:16px;border:1px solid #334155;overflow:hidden;">
<tr><td style="background:linear-gradient(135deg,#0052FF,#6366F1);padding:24px 32px;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr>
<td><h1 style="color:#ffffff;font-size:20px;margin:0;font-weight:700;letter-spacing:-0.5px;">{APP_NAME}</h1>
<p style="color:rgba(255,255,255,0.7);font-size:11px;margin:4px 0 0;">Daily Market Digest</p></td>
<td style="text-align:right;"><p style="color:rgba(255,255,255,0.6);font-size:11px;margin:0;">6:00 AM UTC</p></td>
</tr>
</table>
</td></tr>
<tr><td style="padding:28px 32px;">{content}</td></tr>
<tr><td style="padding:16px 32px;border-top:1px solid #334155;text-align:center;">
<p style="color:#475569;font-size:10px;margin:0;">
<a href="{APP_URL}" style="color:#64748B;text-decoration:none;">Unsubscribe</a> · <a href="{APP_URL}" style="color:#64748B;text-decoration:none;">{APP_URL}</a>
</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


async def send_daily_digest(db):
    """Main entry: collect data, loop through users, send digest emails."""
    from services.email_service import _is_configured, SENDER_EMAIL

    if not _is_configured():
        logger.info("Daily digest skipped: Resend API key not configured")
        return {"sent": 0, "skipped": True, "reason": "no_api_key"}

    # Collect generic digest data
    data = await collect_digest_data(db)
    logger.info(f"Digest data collected: {len(data['predictions'])} predictions, {len(data['dark_pool'])} dark pool, {len(data['signals'])} signals")

    sent_count = 0
    error_count = 0
    wl_count = 0

    # Get all users who haven't opted out
    cursor = db.users.find(
        {"digest_opt_out": {"$ne": True}, "is_active": {"$ne": False}},
        {"email": 1, "name": 1, "subscription_status": 1}
    )

    async for user in cursor:
        email = user.get("email", "")
        name = user.get("name", email.split("@")[0])
        is_pro = user.get("subscription_status") in ("pro", "trial")
        user_id = user.get("_id")

        # Get cached watchlist intelligence for this user
        wl_intel = await get_user_watchlist_intel(db, user_id) if user_id else None
        if wl_intel and wl_intel.get("tickers"):
            wl_count += 1

        html = build_digest_html(data, is_pro, name, watchlist_intel=wl_intel)
        subject = f"Your Morning Market Briefing — {datetime.now(timezone.utc).strftime('%b %d')}"

        try:
            await asyncio.to_thread(resend.Emails.send, {
                "from": SENDER_EMAIL,
                "to": [email],
                "subject": subject,
                "html": html,
            })
            sent_count += 1
            logger.info(f"Digest sent to {email} (pro={is_pro}, wl={'yes' if wl_intel else 'no'})")
        except Exception as e:
            error_count += 1
            logger.error(f"Digest send failed for {email}: {e}")

    logger.info(f"Daily digest complete: {sent_count} sent, {error_count} errors, {wl_count} with watchlist intel")
    return {"sent": sent_count, "errors": error_count, "skipped": False, "with_watchlist": wl_count}
