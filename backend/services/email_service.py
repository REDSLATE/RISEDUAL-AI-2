"""Email notification service with ProviderRouter failover (Resend → SendGrid)."""
import os
import asyncio
import logging
import resend
import httpx
from dotenv import load_dotenv
from pathlib import Path

from services.providerrouter import ProviderRouter
from services.provider_registry import get_email_provider_pool

load_dotenv(Path(__file__).parent.parent / '.env')

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'onboarding@resend.dev')
APP_NAME = "RISEDUAL AI"
APP_URL = os.environ.get('FRONTEND_URL', 'https://risedual.ai')

# Initialize the email provider router
email_router = ProviderRouter("email", get_email_provider_pool())


def _is_configured() -> bool:
    """True when at least one email provider (Resend/SendGrid) has a valid API key."""
    if email_router.providers:
        return True
    return bool(RESEND_API_KEY) and not RESEND_API_KEY.startswith("re_YOUR")


async def _send_via_resend(api_key: str, to: list, subject: str, html: str) -> dict:
    """Send email through Resend API."""
    resend.api_key = api_key
    params = {"from": SENDER_EMAIL, "to": to, "subject": subject, "html": html}
    result = await asyncio.to_thread(resend.Emails.send, params)
    return result


async def _send_via_sendgrid(api_key: str, to: list, subject: str, html: str) -> dict:
    """Send email through SendGrid API."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "personalizations": [{"to": [{"email": e} for e in to]}],
                "from": {"email": SENDER_EMAIL},
                "subject": subject,
                "content": [{"type": "text/html", "value": html}],
            },
        )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"SendGrid {resp.status_code}: {resp.text[:200]}")
        return {"id": "sendgrid", "status": resp.status_code}


async def _routed_send(to: list, subject: str, html: str) -> bool:
    """Send email through the ProviderRouter with failover."""
    if not email_router.providers:
        logger.info(f"Email skipped (no providers configured): {subject} to {to}")
        return False

    async def _dispatch(provider: dict):
        p = provider.get("provider")
        key = provider.get("api_key")
        if p == "resend":
            return await _send_via_resend(key, to, subject, html)
        elif p == "sendgrid":
            return await _send_via_sendgrid(key, to, subject, html)
        else:
            raise RuntimeError(f"Unknown email provider: {p}")

    try:
        routed = await email_router.run(_dispatch)
        provider_name = routed["provider"]["name"]
        result = routed["result"]
        logger.info(f"Email sent via {provider_name}: '{subject}' to {to}, id: {result.get('id', '?')}")
        return True
    except Exception as e:
        logger.error(f"All email providers failed for '{subject}' to {to}: {e}")
        return False


def _base_html(content: str, preheader: str = "") -> str:
    """Email-client safe template.

    Uses light theme + bgcolor attributes for broad compatibility (Gmail, Outlook,
    Apple Mail strip <body> CSS and many container styles; using bgcolor attrs
    ensures content is visible regardless of client quirks).
    """
    pre = (preheader or "").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="color-scheme" content="light only" />
<meta name="supported-color-schemes" content="light only" />
<title>{APP_NAME}</title>
</head>
<body bgcolor="#F1F5F9" style="margin:0;padding:0;background-color:#F1F5F9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#0F172A;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;mso-hide:all;font-size:1px;line-height:1px;">{pre}</div>
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F1F5F9" style="background-color:#F1F5F9;padding:32px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF" style="background-color:#FFFFFF;border-radius:14px;border:1px solid #E2E8F0;max-width:600px;width:100%;">
<!-- Header -->
<tr><td bgcolor="#0F172A" align="center" style="background-color:#0F172A;padding:26px 32px;border-radius:14px 14px 0 0;">
<h1 style="color:#FFFFFF;font-size:22px;margin:0;font-weight:700;letter-spacing:-0.3px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">{APP_NAME}</h1>
<p style="color:#94A3B8;font-size:12px;margin:6px 0 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">AI-Powered Trading Platform</p>
</td></tr>
<!-- Content -->
<tr><td bgcolor="#FFFFFF" style="background-color:#FFFFFF;padding:32px;color:#0F172A;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;font-size:14px;line-height:1.6;">{content}</td></tr>
<!-- Footer -->
<tr><td bgcolor="#F8FAFC" style="background-color:#F8FAFC;padding:20px 32px;border-top:1px solid #E2E8F0;text-align:center;border-radius:0 0 14px 14px;">
<p style="color:#64748B;font-size:11px;margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">You're receiving this because you're a {APP_NAME} member.</p>
<p style="color:#94A3B8;font-size:11px;margin:8px 0 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;"><a href="{APP_URL}" style="color:#0052FF;text-decoration:none;">{APP_URL}</a></p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _referral_signup_html(referrer_name: str, referred_email: str) -> str:
    name = (referrer_name or "there").strip() or "there"
    email = (referred_email or "your friend").strip()
    content = f"""
<h2 style="color:#0F172A;font-size:22px;margin:0 0 8px;font-weight:700;">Your friend just signed up!</h2>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 20px;">
Hey {name}, great news &mdash; someone used your referral link to join {APP_NAME}.
</p>
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F8FAFC" style="background-color:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">New Referral</p>
<p style="color:#0F172A;font-size:16px;margin:0;font-weight:600;">{email}</p>
</td></tr>
</table>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 24px;">
When they subscribe to Pro, you'll earn <strong style="color:#0052FF;">1 free month</strong> of {APP_NAME} Pro. Keep sharing your link!
</p>
<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
<tr><td bgcolor="#0052FF" style="background-color:#0052FF;border-radius:10px;">
<a href="{APP_URL}" style="display:inline-block;color:#FFFFFF;text-decoration:none;font-size:14px;font-weight:600;padding:12px 28px;">View Your Referrals</a>
</td></tr>
</table>"""
    return _base_html(content, preheader=f"{email} just joined {APP_NAME} using your link.")


def _reward_earned_html(referrer_name: str, referred_email: str) -> str:
    name = (referrer_name or "there").strip() or "there"
    email = (referred_email or "your referral").strip()
    content = f"""
<h2 style="color:#0F172A;font-size:22px;margin:0 0 8px;font-weight:700;">You earned a free month!</h2>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 20px;">
Congrats {name}! Your referral just subscribed to Pro, and you've earned <strong style="color:#10B981;">1 free month</strong> of {APP_NAME} Pro.
</p>
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F8FAFC" style="background-color:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
<td width="50%" style="vertical-align:top;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Referral</p>
<p style="color:#0F172A;font-size:14px;margin:0;font-weight:500;">{email}</p>
</td>
<td width="50%" style="text-align:right;vertical-align:top;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Reward</p>
<p style="color:#10B981;font-size:18px;margin:0;font-weight:700;">+1 Month Free</p>
</td>
</tr>
</table>
</td></tr>
</table>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 24px;">
Keep referring friends to earn more free months (up to 12 per year).
</p>
<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
<tr><td bgcolor="#0052FF" style="background-color:#0052FF;border-radius:10px;">
<a href="{APP_URL}" style="display:inline-block;color:#FFFFFF;text-decoration:none;font-size:14px;font-weight:600;padding:12px 28px;">Share Your Link</a>
</td></tr>
</table>"""
    return _base_html(content, preheader=f"You earned 1 free month of {APP_NAME} Pro thanks to {email}.")


def _welcome_referral_html(user_name: str, referrer_name: str) -> str:
    name = (user_name or "there").strip() or "there"
    referrer = (referrer_name or "a friend").strip()
    content = f"""
<h2 style="color:#0F172A;font-size:22px;margin:0 0 8px;font-weight:700;">Welcome to {APP_NAME}!</h2>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 20px;">
Hey {name}, welcome aboard. You were referred by <strong style="color:#0F172A;">{referrer}</strong>, and we've activated a special gift for you.
</p>
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#EFF6FF" style="background-color:#EFF6FF;border-radius:10px;border:1px solid #BFDBFE;margin-bottom:20px;">
<tr><td align="center" style="padding:20px;">
<p style="color:#0052FF;font-size:12px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1.5px;font-weight:700;">Your Gift</p>
<p style="color:#0F172A;font-size:24px;margin:0;font-weight:800;">7-Day Pro Trial</p>
<p style="color:#475569;font-size:13px;margin:8px 0 0;">Full access to AI predictions, dark pool data, market signals &amp; more.</p>
</td></tr>
</table>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 24px;">
Dive in and explore everything {APP_NAME} has to offer. Your Pro trial starts now.
</p>
<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
<tr><td bgcolor="#0052FF" style="background-color:#0052FF;border-radius:10px;">
<a href="{APP_URL}" style="display:inline-block;color:#FFFFFF;text-decoration:none;font-size:14px;font-weight:600;padding:12px 28px;">Start Exploring</a>
</td></tr>
</table>"""
    return _base_html(content, preheader=f"Your 7-day Pro trial is active, {name}. Let's get started.")


async def send_referral_signup_email(referrer_email: str, referrer_name: str, referred_email: str):
    """Notify referrer when someone signs up via their link."""
    return await _routed_send(
        [referrer_email],
        f"Your friend just joined {APP_NAME}!",
        _referral_signup_html(referrer_name, referred_email),
    )


async def send_reward_earned_email(referrer_email: str, referrer_name: str, referred_email: str):
    """Notify referrer when they earn a free month."""
    return await _routed_send(
        [referrer_email],
        f"You earned a free month of {APP_NAME} Pro!",
        _reward_earned_html(referrer_name, referred_email),
    )


def _password_reset_html(reset_url: str) -> str:
    content = f"""
<h2 style="color:#0F172A;font-size:22px;margin:0 0 8px;font-weight:700;">Reset Your Password</h2>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 20px;">
We received a request to reset your {APP_NAME} password. Click the button below to choose a new one.
</p>
<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto 20px;">
<tr><td bgcolor="#0052FF" style="background-color:#0052FF;border-radius:10px;">
<a href="{reset_url}" style="display:inline-block;color:#FFFFFF;text-decoration:none;font-size:14px;font-weight:600;padding:14px 32px;">Reset Password</a>
</td></tr>
</table>
<p style="color:#64748B;font-size:12px;line-height:1.6;margin:0 0 12px;">
If the button doesn't work, copy and paste this link into your browser:
</p>
<p style="color:#0052FF;font-size:12px;word-break:break-all;margin:0 0 20px;">
<a href="{reset_url}" style="color:#0052FF;text-decoration:underline;">{reset_url}</a>
</p>
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#FEF3C7" style="background-color:#FEF3C7;border-radius:10px;border:1px solid #FDE68A;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<p style="color:#92400E;font-size:12px;margin:0 0 4px;font-weight:700;">This link expires in 1 hour</p>
<p style="color:#78350F;font-size:12px;margin:0;">If you didn't request this, you can safely ignore this email.</p>
</td></tr>
</table>"""
    return _base_html(content, preheader=f"Reset your {APP_NAME} password. Link expires in 1 hour.")


async def send_password_reset_email(user_email: str, reset_token: str, origin_url: str = None):
    """Send a password reset email with a secure link."""
    base_url = origin_url or os.environ.get('FRONTEND_URL', APP_URL)
    reset_url = f"{base_url}?reset_token={reset_token}"
    sent = await _routed_send(
        [user_email],
        f"Reset Your {APP_NAME} Password",
        _password_reset_html(reset_url),
    )
    if not sent:
        logger.info(f"[PASSWORD RESET LINK] {reset_url}")
    return sent


def _toxic_spikes_html(toxic_count: int, obsolete_count: int, total_before: int, total_after: int, spike_details: list) -> str:
    """Email template for Toxic Spikes Alert — sent when nightly cleanup detects bad predictions."""
    detail_rows = ""
    for spike in spike_details[:10]:  # Cap at 10 examples
        detail_rows += f"""<tr>
<td style="padding:8px 12px;color:#0F172A;font-size:13px;border-bottom:1px solid #E2E8F0;">{spike.get('symbol','?')}</td>
<td style="padding:8px 12px;color:#DC2626;font-size:13px;border-bottom:1px solid #E2E8F0;font-weight:600;">{spike.get('confidence','?')}%</td>
<td style="padding:8px 12px;color:#64748B;font-size:13px;border-bottom:1px solid #E2E8F0;">{spike.get('date','?')}</td>
</tr>"""

    details_table = ""
    if detail_rows:
        details_table = f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F8FAFC" style="background-color:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:20px;border-collapse:collapse;">
<tr>
<th style="padding:10px 12px;color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px;text-align:left;border-bottom:1px solid #E2E8F0;font-weight:700;">Ticker</th>
<th style="padding:10px 12px;color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px;text-align:left;border-bottom:1px solid #E2E8F0;font-weight:700;">Confidence</th>
<th style="padding:10px 12px;color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px;text-align:left;border-bottom:1px solid #E2E8F0;font-weight:700;">Date</th>
</tr>
{detail_rows}
</table>"""

    content = f"""
<h2 style="color:#DC2626;font-size:22px;margin:0 0 8px;font-weight:700;">Toxic Spikes Detected</h2>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 20px;">
The nightly memory cleanup found <strong style="color:#DC2626;">{toxic_count} high-confidence failures</strong> in the AI prediction engine. These have been re-tagged as negative lessons so the AI avoids repeating these mistakes.
</p>
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F8FAFC" style="background-color:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
<td width="33%" style="text-align:center;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Toxic Removed</p>
<p style="color:#DC2626;font-size:22px;margin:0;font-weight:800;">{toxic_count}</p>
</td>
<td width="33%" style="text-align:center;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Obsolete Pruned</p>
<p style="color:#D97706;font-size:22px;margin:0;font-weight:800;">{obsolete_count}</p>
</td>
<td width="33%" style="text-align:center;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:600;">Episodes Now</p>
<p style="color:#059669;font-size:22px;margin:0;font-weight:800;">{total_after}</p>
</td>
</tr>
</table>
</td></tr>
</table>
{details_table}
<p style="color:#475569;font-size:13px;line-height:1.6;margin:0 0 24px;">
These toxic patterns are preserved in ChromaDB as <strong style="color:#D97706;">negative lessons</strong> &mdash; the AI will use them to avoid similar high-confidence errors in the future.
</p>
<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
<tr><td bgcolor="#0052FF" style="background-color:#0052FF;border-radius:10px;">
<a href="{APP_URL}" style="display:inline-block;color:#FFFFFF;text-decoration:none;font-size:14px;font-weight:600;padding:12px 28px;">View Memory Dashboard</a>
</td></tr>
</table>"""
    return _base_html(content, preheader=f"{toxic_count} toxic predictions detected and re-tagged.")


async def send_toxic_spikes_email(
    recipient_email: str,
    toxic_count: int,
    obsolete_count: int,
    total_before: int,
    total_after: int,
    spike_details: list = None,
    persistence_tag: str = "",
):
    """Send toxic spikes alert email after nightly cleanup detects bad
    predictions. `persistence_tag` is appended to the subject when the
    same alert has been recurring on consecutive days (e.g. " —
    Persisting (3 days in a row)"); empty string by default for the
    first occurrence."""
    if not email_router.providers:
        logger.info(f"Email skipped (no providers configured): toxic spikes alert to {recipient_email}")
        return False
    try:
        params = {
            "from": SENDER_EMAIL,
            "to": [recipient_email],
            "subject": f"[{APP_NAME}] Toxic Spikes Alert — {toxic_count} High-Confidence Failures Detected{persistence_tag}",
            "html": _toxic_spikes_html(
                toxic_count, obsolete_count, total_before, total_after, spike_details or []
            ),
        }
        result = await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Toxic spikes alert email sent to {recipient_email}, id: {result.get('id', 'unknown')}")
        return True
    except Exception as e:
        logger.error(f"Failed to send toxic spikes email to {recipient_email}: {e}")
        return False


async def send_welcome_referral_email(user_email: str, user_name: str, referrer_name: str):
    """Send welcome email to newly referred user."""
    return await _routed_send(
        [user_email],
        f"Welcome to {APP_NAME} — Your 7-Day Pro Trial is Active!",
        _welcome_referral_html(user_name, referrer_name),
    )


# ── Tiered Referral Reward Emails (Smart Money Board) ──

_REWARD_KIND_META = {
    "trial_pro_max": {
        "label": "Pro Max Trial",
        "unit": "days",
        "accent": "#7C3AED",
        "emoji": "&#x1F451;",  # 👑
    },
    "trial_pro": {
        "label": "Pro Trial",
        "unit": "days",
        "accent": "#0052FF",
        "emoji": "&#x1F3C6;",  # 🏆
    },
    "credits": {
        "label": "Credits",
        "unit": "credits",
        "accent": "#059669",
        "emoji": "&#x1F3AF;",  # 🎯
    },
}


def _tiered_reward_html(
    user_name: str,
    tier: str,
    kind: str,
    amount: int,
    hits: int,
    rank: int | None = None,
    period: str | None = None,
) -> str:
    name = (user_name or "Trader").strip() or "Trader"
    meta = _REWARD_KIND_META.get(kind, _REWARD_KIND_META["credits"])
    accent = meta["accent"]
    label = meta["label"]
    unit = meta["unit"]
    emoji = meta["emoji"]

    if tier == "hits_threshold":
        title = "You unlocked a 7-day Pro trial"
        sub = f"Your Smart Money Board reached {hits} scans this month."
        blurb = f"Enjoy full access to AI predictions, dark pool data, and all premium signals for {amount} days."
    elif tier == "monthly_winner":
        title = "You won the Smart Money Leaderboard"
        sub = f"#1 for {period or 'last month'} with {hits} scans."
        blurb = f"{amount} days of Pro Max on us &mdash; the highest tier unlocked."
    elif tier == "monthly_runner_up":
        title = "Runner-up on last month's leaderboard"
        sub = f"Ranked #{rank} with {hits} scans."
        blurb = f"{amount} days of Pro, on the house."
    elif tier == "monthly_finalist":
        title = "Top 5 on last month's leaderboard"
        sub = f"Ranked #{rank} with {hits} scans."
        blurb = f"{amount} RiseDual credits have been added to your wallet."
    else:
        title = "Referral reward unlocked"
        sub = f"{hits} scans this month."
        blurb = f"You earned {amount} {unit}."

    content = f"""
<p style="color:{accent};font-size:28px;margin:0 0 4px;line-height:1;">{emoji}</p>
<h2 style="color:#0F172A;font-size:22px;margin:0 0 8px;font-weight:800;">{title}</h2>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 20px;">
Congrats {name} &mdash; {sub}
</p>
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F8FAFC" style="background-color:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:20px;">
<tr><td align="center" style="padding:20px;">
<p style="color:#64748B;font-size:11px;margin:0 0 6px;text-transform:uppercase;letter-spacing:1.5px;font-weight:700;">Your Reward</p>
<p style="color:{accent};font-size:30px;margin:0;font-weight:800;">{amount} {unit}</p>
<p style="color:#475569;font-size:13px;margin:6px 0 0;">{label}</p>
</td></tr>
</table>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 24px;">
{blurb}
</p>
<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
<tr><td bgcolor="{accent}" style="background-color:{accent};border-radius:10px;">
<a href="{APP_URL}" style="display:inline-block;color:#FFFFFF;text-decoration:none;font-size:14px;font-weight:600;padding:12px 28px;">Open {APP_NAME}</a>
</td></tr>
</table>
<p style="color:#94A3B8;font-size:11px;margin:20px 0 0;text-align:center;">
Keep sharing your Smart Money Board &mdash; monthly #1 wins Pro Max.
</p>"""
    return _base_html(content, preheader=f"You earned {amount} {unit} — {label}.")


async def send_tiered_reward_email(
    user_email: str,
    user_name: str,
    tier: str,
    kind: str,
    amount: int,
    hits: int,
    rank: int | None = None,
    period: str | None = None,
) -> bool:
    """Email notification when a user earns a tiered referral reward
    (5-hit threshold, monthly leaderboard top 5).
    """
    if not user_email:
        return False

    meta = _REWARD_KIND_META.get(kind, _REWARD_KIND_META["credits"])
    subject_map = {
        "hits_threshold": f"You unlocked {amount} days of {APP_NAME} Pro",
        "monthly_winner": f"You won: {amount} days of {APP_NAME} Pro Max",
        "monthly_runner_up": f"Runner-up: {amount} days of {APP_NAME} Pro",
        "monthly_finalist": f"Top 5: {amount} {APP_NAME} credits",
    }
    subject = subject_map.get(tier, f"You earned {amount} {meta['unit']}")

    return await _routed_send(
        [user_email],
        subject,
        _tiered_reward_html(user_name, tier, kind, amount, hits, rank=rank, period=period),
    )



# ── Help Search Weekly Digest (Admin Feature-Gap Radar) ──

def _help_search_digest_html(admin_name: str, data: dict) -> str:
    """HTML for the weekly zero-result search digest sent to admins.

    ``data`` shape: ``{total, zero_total, zero_rate, zero_top: [{q, count, unique_users, hubs}]}``.
    """
    name = (admin_name or "there").strip() or "there"
    total = int(data.get("total") or 0)
    zero_total = int(data.get("zero_total") or 0)
    zero_rate_pct = f"{(data.get('zero_rate') or 0) * 100:.1f}%"

    # Gap-signal badge color
    rate = float(data.get("zero_rate") or 0)
    if rate > 0.2:
        gap_color, gap_bg, gap_label = "#DC2626", "#FEE2E2", "HIGH"
    elif rate > 0.1:
        gap_color, gap_bg, gap_label = "#D97706", "#FEF3C7", "MEDIUM"
    else:
        gap_color, gap_bg, gap_label = "#059669", "#D1FAE5", "LOW"

    # Top zero-result table rows
    rows_html = ""
    for i, r in enumerate(data.get("zero_top") or []):
        q_safe = (r.get("q") or "").replace("<", "&lt;").replace(">", "&gt;")
        hubs = ", ".join(r.get("hubs") or []) or "—"
        uu = int(r.get("unique_users") or 0)
        rank_bg = "#F8FAFC" if i % 2 == 0 else "#FFFFFF"
        rows_html += f"""<tr bgcolor="{rank_bg}" style="background-color:{rank_bg};">
<td style="padding:10px 14px;border-bottom:1px solid #E2E8F0;color:#0F172A;font-size:13px;font-weight:600;">{q_safe}</td>
<td style="padding:10px 14px;border-bottom:1px solid #E2E8F0;color:#DC2626;font-size:14px;font-weight:800;text-align:right;">{r.get('count', 0)}</td>
<td style="padding:10px 14px;border-bottom:1px solid #E2E8F0;color:#475569;font-size:12px;text-align:right;">{uu} user{'s' if uu != 1 else ''}</td>
<td style="padding:10px 14px;border-bottom:1px solid #E2E8F0;color:#64748B;font-size:11px;">{hubs}</td>
</tr>"""

    top_table = f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF" style="background-color:#FFFFFF;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:20px;border-collapse:collapse;">
<tr bgcolor="#0F172A" style="background-color:#0F172A;">
<th align="left" style="padding:10px 14px;color:#94A3B8;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:700;">Query</th>
<th align="right" style="padding:10px 14px;color:#94A3B8;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:700;">Count</th>
<th align="right" style="padding:10px 14px;color:#94A3B8;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:700;">Users</th>
<th align="left" style="padding:10px 14px;color:#94A3B8;font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:700;">Context</th>
</tr>
{rows_html}
</table>"""

    # Headline insight — one punchy call-out for the top gap
    top = (data.get("zero_top") or [{}])[0]
    top_q = (top.get("q") or "").replace("<", "&lt;").replace(">", "&gt;")
    top_count = int(top.get("count") or 0)
    top_users = int(top.get("unique_users") or 0)
    headline = ""
    if top_q:
        headline = f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#FEF3C7" style="background-color:#FEF3C7;border-radius:10px;border:1px solid #FDE68A;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<p style="color:#92400E;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1.5px;font-weight:700;">Biggest Gap This Week</p>
<p style="color:#0F172A;font-size:18px;margin:0 0 4px;font-weight:800;">&ldquo;{top_q}&rdquo;</p>
<p style="color:#78350F;font-size:12px;margin:0;">{top_count} search{'es' if top_count != 1 else ''} · {top_users} unique user{'s' if top_users != 1 else ''} · 0 results returned.</p>
</td></tr>
</table>"""

    content = f"""
<h2 style="color:#0F172A;font-size:22px;margin:0 0 4px;font-weight:800;">Feature-Gap Radar</h2>
<p style="color:#64748B;font-size:12px;margin:0 0 20px;font-weight:600;">7-day summary &mdash; what users searched for but couldn&rsquo;t find.</p>

<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 20px;">
Hey {name}, here&rsquo;s what {APP_NAME} users searched for in the Help Center this week.
Zero-result queries are the fastest signal for your next feature or doc.
</p>

<!-- KPI row -->
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F8FAFC" style="background-color:#F8FAFC;border-radius:10px;border:1px solid #E2E8F0;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
<td width="33%" style="text-align:center;vertical-align:top;">
<p style="color:#64748B;font-size:10px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:700;">Total</p>
<p style="color:#0F172A;font-size:22px;margin:0;font-weight:800;">{total}</p>
<p style="color:#94A3B8;font-size:10px;margin:2px 0 0;">searches</p>
</td>
<td width="34%" style="text-align:center;vertical-align:top;border-left:1px solid #E2E8F0;border-right:1px solid #E2E8F0;">
<p style="color:#64748B;font-size:10px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:700;">Zero-Result</p>
<p style="color:#DC2626;font-size:22px;margin:0;font-weight:800;">{zero_total}</p>
<p style="color:#94A3B8;font-size:10px;margin:2px 0 0;">{zero_rate_pct} of total</p>
</td>
<td width="33%" style="text-align:center;vertical-align:top;">
<p style="color:#64748B;font-size:10px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;font-weight:700;">Gap Signal</p>
<p style="margin:0;"><span style="display:inline-block;background-color:{gap_bg};color:{gap_color};font-size:12px;font-weight:800;padding:4px 10px;border-radius:6px;">{gap_label}</span></p>
<p style="color:#94A3B8;font-size:10px;margin:4px 0 0;">&gt;20% = ship docs/features</p>
</td>
</tr>
</table>
</td></tr>
</table>

{headline}

<p style="color:#64748B;font-size:11px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1.5px;font-weight:700;">Top Zero-Result Queries</p>
{top_table}

<p style="color:#475569;font-size:13px;line-height:1.6;margin:0 0 24px;">
Every item above is a user who hit the Help Center and walked away without an answer.
Fix the doc, build the feature, or add the integration &mdash; fastest path to reducing friction.
</p>

<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
<tr><td bgcolor="#0052FF" style="background-color:#0052FF;border-radius:10px;">
<a href="{APP_URL}/?v2=1" style="display:inline-block;color:#FFFFFF;text-decoration:none;font-size:14px;font-weight:600;padding:12px 28px;">Open Admin Panel</a>
</td></tr>
</table>"""

    preheader = (f"{zero_total} zero-result searches this week"
                 + (f" · top: '{top_q}'" if top_q else ""))
    return _base_html(content, preheader=preheader)


# ── Waitlist Emails ──
# ── Waitlist Emails ──

def _war_room_invite_html(name: str, beta_key: str, rank: int, referral_count: int) -> str:
    """HTML email for War Room beta invite — the user has been bumped to the front."""
    display_name = (name or "Trader").strip() or "Trader"
    key_display = (beta_key or "").strip() or "—"
    referral_line = ""
    if referral_count and referral_count > 0:
        plural = "s" if referral_count != 1 else ""
        referral_line = f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#EEF2FF" style="background-color:#EEF2FF;border-radius:10px;border:1px solid #C7D2FE;margin-bottom:16px;">
<tr><td style="padding:12px 16px;">
<p style="color:#4338CA;font-size:13px;margin:0;"><strong>{referral_count} referral{plural}</strong> helped you skip the line.</p>
</td></tr>
</table>"""

    content = f"""
<h2 style="color:#0F172A;font-size:24px;margin:0 0 4px;font-weight:800;">You've been bumped to the front.</h2>
<p style="color:#0D9488;font-size:14px;margin:0 0 20px;font-weight:700;">Welcome to the War Room, {display_name}.</p>
<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 24px;">
You were <strong style="color:#0F172A;">#{rank}</strong> in priority. Our adversarial AI system &mdash; where the
<span style="color:#0D9488;font-weight:600;">Strategist</span> generates signals and the
<span style="color:#EA580C;font-weight:600;">Auditor</span> kills the bad ones &mdash; is now unlocked for you.
</p>
{referral_line}
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0F172A" style="background-color:#0F172A;border-radius:12px;margin-bottom:24px;">
<tr><td align="center" style="padding:24px 20px;">
<p style="color:#94A3B8;font-size:11px;margin:0 0 10px;text-transform:uppercase;letter-spacing:2px;font-weight:600;">Your Beta Access Key</p>
<p style="color:#3DE8D9;font-size:28px;font-weight:800;margin:0;font-family:'Courier New',monospace;letter-spacing:3px;">{key_display}</p>
</td></tr>
</table>
<p style="color:#475569;font-size:13px;line-height:1.6;margin:0 0 24px;">
Use this key to activate your beta account. As an early tester, every trade, prediction, and signal you interact with
<strong style="color:#0F172A;">feeds our AI pipeline</strong> &mdash; making the system smarter for everyone.
</p>
<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
<tr><td bgcolor="#0D9488" style="background-color:#0D9488;border-radius:12px;">
<a href="{APP_URL}" style="display:inline-block;color:#FFFFFF;font-size:14px;font-weight:600;padding:14px 32px;text-decoration:none;">Enter the War Room &rarr;</a>
</td></tr>
</table>
<p style="color:#94A3B8;font-size:11px;margin:24px 0 0;text-align:center;">
This key is unique to you. Do not share it. It expires in 7 days.
</p>
"""
    return _base_html(content, preheader=f"Your War Room beta access key: {key_display}")


def _referral_success_html(name: str, new_rank: int, referral_count: int, spots_skipped: int) -> str:
    """HTML email when a referral successfully joins — the referrer skipped the line."""
    display_name = (name or "Trader").strip() or "Trader"
    content = f"""
<h2 style="color:#0F172A;font-size:22px;margin:0 0 4px;font-weight:800;">You just skipped the line.</h2>
<p style="color:#7C3AED;font-size:14px;margin:0 0 20px;font-weight:700;">{display_name}, your referral landed.</p>

<p style="color:#475569;font-size:14px;line-height:1.6;margin:0 0 24px;">
Someone used your referral link and you just jumped <strong style="color:#0D9488;">{spots_skipped} spots</strong> closer to the front.
</p>

<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:24px;">
<tr>
<td width="33%" bgcolor="#F8FAFC" align="center" style="background-color:#F8FAFC;padding:16px 8px;border:1px solid #E2E8F0;border-radius:10px 0 0 10px;">
<p style="color:#0D9488;font-size:24px;font-weight:800;margin:0;">#{new_rank}</p>
<p style="color:#64748B;font-size:10px;margin:4px 0 0;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">New Rank</p>
</td>
<td width="33%" bgcolor="#F8FAFC" align="center" style="background-color:#F8FAFC;padding:16px 8px;border-top:1px solid #E2E8F0;border-bottom:1px solid #E2E8F0;">
<p style="color:#7C3AED;font-size:24px;font-weight:800;margin:0;">{referral_count}</p>
<p style="color:#64748B;font-size:10px;margin:4px 0 0;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">Referrals</p>
</td>
<td width="33%" bgcolor="#F8FAFC" align="center" style="background-color:#F8FAFC;padding:16px 8px;border:1px solid #E2E8F0;border-radius:0 10px 10px 0;"><p style="color:#EA580C;font-size:24px;font-weight:800;margin:0;">{spots_skipped}</p>
<p style="color:#64748B;font-size:10px;margin:4px 0 0;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">Spots Skipped</p>
</td>
</tr>
</table>

<p style="color:#475569;font-size:13px;line-height:1.6;margin:0 0 20px;">
Keep sharing. The top 100 in the priority queue become <strong style="color:#0F172A;">Founding Members</strong> &mdash;
lifetime perks, exclusive badge, and first access to every new feature.
</p>

<table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;">
<tr><td bgcolor="#7C3AED" style="background-color:#7C3AED;border-radius:10px;">
<a href="{APP_URL}" style="display:inline-block;color:#FFFFFF;font-size:14px;font-weight:600;padding:12px 28px;text-decoration:none;">Share Again &rarr;</a>
</td></tr>
</table>
"""
    return _base_html(content, preheader=f"You skipped {spots_skipped} spots — now #{new_rank} in line.")


async def send_war_room_invite(email: str, name: str, beta_key: str, rank: int, referral_count: int) -> bool:
    """Send the War Room beta invite email with access key."""
    return await _routed_send(
        [email],
        "You've been bumped to the front: Welcome to the War Room.",
        _war_room_invite_html(name, beta_key, rank, referral_count),
    )


async def send_referral_success(email: str, name: str, new_rank: int, referral_count: int, spots_skipped: int) -> bool:
    """Send referral success notification — you just skipped the line."""
    if not email_router.providers:
        logger.warning("No email providers configured — skipping referral success email")
        return False
    try:
        params = {
            "from": SENDER_EMAIL,
            "to": [email],
            "subject": f"You just skipped 20 spots — now #{new_rank} in line",
            "html": _referral_success_html(name, new_rank, referral_count, spots_skipped),
        }
        result = await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Referral success email sent to {email}, id: {result.get('id', 'unknown')}")
        return True
    except Exception as e:
        logger.error(f"Failed to send referral success email to {email}: {e}")
        return False
