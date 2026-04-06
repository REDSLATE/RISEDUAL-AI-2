"""Daily digest email service - collects market data and sends morning briefing."""
import logging
import asyncio
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


def build_digest_html(data: Dict, is_pro: bool, user_name: str) -> str:
    """Build the digest HTML email. Pro users get full data, free users get teaser."""
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # Predictions section
    predictions_html = ""
    for i, p in enumerate(data.get("predictions", [])[:3]):
        if not is_pro and i >= 1:
            predictions_html += f"""<tr>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:#475569;font-size:12px;filter:blur(4px);">██████</td>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:#475569;font-size:12px;text-align:right;filter:blur(4px);">████</td>
</tr>"""
        else:
            vc = _verdict_color(p["verdict"])
            predictions_html += _row(
                f"{p['ticker']}",
                f"{p['verdict']} ({p['confidence']}%)",
                vc
            )

    # Dark pool section
    dark_pool_html = ""
    for i, dp in enumerate(data.get("dark_pool", [])[:5]):
        if not is_pro and i >= 1:
            dark_pool_html += f"""<tr>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:#475569;font-size:12px;filter:blur(4px);">██████</td>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:#475569;font-size:12px;text-align:right;filter:blur(4px);">████</td>
</tr>"""
        else:
            sc = "#10B981" if dp["sentiment"] == "bullish" else "#EF4444" if dp["sentiment"] == "bearish" else "#F59E0B"
            dark_pool_html += _row(dp["ticker"], f"{dp['sentiment'].title()}", sc)

    # Signals section
    signals_html = ""
    for i, s in enumerate(data.get("signals", [])[:5]):
        if not is_pro and i >= 1:
            signals_html += f"""<tr>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:#475569;font-size:12px;filter:blur(4px);">██████</td>
<td style="padding:8px 12px;border-bottom:1px solid #334155;color:#475569;font-size:12px;text-align:right;filter:blur(4px);">████</td>
</tr>"""
        else:
            signals_html += _row(s["ticker"], f"{s['signal']} ({s['strength']})")

    upgrade_cta = "" if is_pro else f"""
<table width="100%" cellpadding="0" cellspacing="0" style="background:linear-gradient(135deg,#0052FF20,#6366F120);border-radius:12px;border:1px solid #0052FF40;margin:20px 0;">
<tr><td style="padding:16px;text-align:center;">
<p style="color:#0052FF;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Unlock Full Digest</p>
<p style="color:#94A3B8;font-size:13px;margin:0 0 12px;">Upgrade to Pro to see all predictions, dark pool moves, and signals</p>
<a href="{APP_URL}" style="display:inline-block;background-color:#0052FF;color:#ffffff;text-decoration:none;font-size:13px;font-weight:600;padding:10px 24px;border-radius:8px;">Upgrade to Pro — $45/mo</a>
</td></tr>
</table>"""

    no_data_msg = '<tr><td colspan="2" style="padding:12px;color:#64748B;font-size:12px;text-align:center;">No recent data available</td></tr>'

    content = f"""
<h2 style="color:#ffffff;font-size:20px;margin:0 0 4px;font-weight:600;">Good Morning, {user_name}</h2>
<p style="color:#64748B;font-size:12px;margin:0 0 20px;">{date_str} — Daily Market Digest</p>

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
    from services.email_service import _is_configured, SENDER_EMAIL, APP_NAME as EMAIL_APP_NAME
    import resend

    if not _is_configured():
        logger.info("Daily digest skipped: Resend API key not configured")
        return {"sent": 0, "skipped": True, "reason": "no_api_key"}

    # Collect digest data
    data = await collect_digest_data(db)
    logger.info(f"Digest data collected: {len(data['predictions'])} predictions, {len(data['dark_pool'])} dark pool, {len(data['signals'])} signals")

    sent_count = 0
    error_count = 0

    # Get all users who haven't opted out
    cursor = db.users.find(
        {"digest_opt_out": {"$ne": True}, "is_active": {"$ne": False}},
        {"_id": 0, "email": 1, "name": 1, "subscription_status": 1}
    )

    async for user in cursor:
        email = user.get("email", "")
        name = user.get("name", email.split("@")[0])
        is_pro = user.get("subscription_status") in ("pro", "trial")

        html = build_digest_html(data, is_pro, name)
        subject = f"Your Morning Market Briefing — {datetime.now(timezone.utc).strftime('%b %d')}"

        try:
            result = await asyncio.to_thread(resend.Emails.send, {
                "from": SENDER_EMAIL,
                "to": [email],
                "subject": subject,
                "html": html,
            })
            sent_count += 1
            logger.info(f"Digest sent to {email} (pro={is_pro})")
        except Exception as e:
            error_count += 1
            logger.error(f"Digest send failed for {email}: {e}")

    logger.info(f"Daily digest complete: {sent_count} sent, {error_count} errors")
    return {"sent": sent_count, "errors": error_count, "skipped": False}
