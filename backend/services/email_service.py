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

SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'onboarding@resend.dev')
APP_NAME = "RISEDUAL AI"
APP_URL = os.environ.get('FRONTEND_URL', 'https://risedual.ai')

# Initialize the email provider router
email_router = ProviderRouter("email", get_email_provider_pool())


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


def _base_html(content: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#0F172A;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;padding:40px 20px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background-color:#1E293B;border-radius:16px;border:1px solid #334155;overflow:hidden;">
<!-- Header -->
<tr><td style="background:linear-gradient(135deg,#0052FF,#6366F1);padding:28px 32px;text-align:center;">
<h1 style="color:#ffffff;font-size:22px;margin:0;font-weight:700;letter-spacing:-0.5px;">{APP_NAME}</h1>
<p style="color:rgba(255,255,255,0.7);font-size:12px;margin:6px 0 0;">AI-Powered Trading Platform</p>
</td></tr>
<!-- Content -->
<tr><td style="padding:32px;">{content}</td></tr>
<!-- Footer -->
<tr><td style="padding:20px 32px;border-top:1px solid #334155;text-align:center;">
<p style="color:#64748B;font-size:11px;margin:0;">You're receiving this because you're a {APP_NAME} member.</p>
<p style="color:#475569;font-size:11px;margin:8px 0 0;"><a href="{APP_URL}" style="color:#0052FF;text-decoration:none;">{APP_URL}</a></p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _referral_signup_html(referrer_name: str, referred_email: str) -> str:
    content = f"""
<h2 style="color:#ffffff;font-size:20px;margin:0 0 8px;font-weight:600;">Your friend just signed up!</h2>
<p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0 0 20px;">
Hey {referrer_name}, great news &mdash; someone used your referral link to join {APP_NAME}!
</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:12px;border:1px solid #334155;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;">New Referral</p>
<p style="color:#ffffff;font-size:16px;margin:0;font-weight:600;">{referred_email}</p>
</td></tr>
</table>
<p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0 0 24px;">
When they subscribe to Pro, you'll earn <strong style="color:#0052FF;">1 free month</strong> of {APP_NAME} Pro. Keep sharing your link!
</p>
<table cellpadding="0" cellspacing="0" style="margin:0 auto;">
<tr><td style="background-color:#0052FF;border-radius:10px;padding:12px 28px;">
<a href="{APP_URL}" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">View Your Referrals</a>
</td></tr>
</table>"""
    return _base_html(content)


def _reward_earned_html(referrer_name: str, referred_email: str) -> str:
    content = f"""
<h2 style="color:#ffffff;font-size:20px;margin:0 0 8px;font-weight:600;">You earned a free month!</h2>
<p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0 0 20px;">
Congrats {referrer_name}! Your referral just subscribed to Pro, and you've earned <strong style="color:#10B981;">1 free month</strong> of {APP_NAME} Pro.
</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:12px;border:1px solid #334155;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr>
<td style="width:50%;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;">Referral</p>
<p style="color:#ffffff;font-size:14px;margin:0;font-weight:500;">{referred_email}</p>
</td>
<td style="width:50%;text-align:right;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;">Reward</p>
<p style="color:#10B981;font-size:18px;margin:0;font-weight:700;">+1 Month Free</p>
</td>
</tr>
</table>
</td></tr>
</table>
<p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0 0 24px;">
Keep referring friends to earn more free months (up to 12 per year)!
</p>
<table cellpadding="0" cellspacing="0" style="margin:0 auto;">
<tr><td style="background-color:#0052FF;border-radius:10px;padding:12px 28px;">
<a href="{APP_URL}" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">Share Your Link</a>
</td></tr>
</table>"""
    return _base_html(content)


def _welcome_referral_html(user_name: str, referrer_name: str) -> str:
    content = f"""
<h2 style="color:#ffffff;font-size:20px;margin:0 0 8px;font-weight:600;">Welcome to {APP_NAME}!</h2>
<p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0 0 20px;">
Hey {user_name}, welcome aboard! You were referred by <strong style="color:#ffffff;">{referrer_name}</strong>, and we've activated a special gift for you.
</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background:linear-gradient(135deg,#0052FF20,#6366F120);border-radius:12px;border:1px solid #0052FF40;margin-bottom:20px;">
<tr><td style="padding:20px;text-align:center;">
<p style="color:#0052FF;font-size:12px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1.5px;font-weight:600;">Your Gift</p>
<p style="color:#ffffff;font-size:24px;margin:0;font-weight:700;">7-Day Pro Trial</p>
<p style="color:#94A3B8;font-size:13px;margin:8px 0 0;">Full access to AI predictions, dark pool data, market signals & more</p>
</td></tr>
</table>
<p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0 0 24px;">
Dive in and explore everything {APP_NAME} has to offer. Your Pro trial starts now!
</p>
<table cellpadding="0" cellspacing="0" style="margin:0 auto;">
<tr><td style="background-color:#0052FF;border-radius:10px;padding:12px 28px;">
<a href="{APP_URL}" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">Start Exploring</a>
</td></tr>
</table>"""
    return _base_html(content)


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
<h2 style="color:#ffffff;font-size:20px;margin:0 0 8px;font-weight:600;">Reset Your Password</h2>
<p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0 0 20px;">
We received a request to reset your {APP_NAME} password. Click the button below to choose a new one.
</p>
<table cellpadding="0" cellspacing="0" style="margin:0 auto 20px;">
<tr><td style="background-color:#0052FF;border-radius:10px;padding:14px 32px;">
<a href="{reset_url}" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">Reset Password</a>
</td></tr>
</table>
<p style="color:#64748B;font-size:12px;line-height:1.6;margin:0 0 12px;">
If the button doesn't work, copy and paste this link into your browser:
</p>
<p style="color:#0052FF;font-size:12px;word-break:break-all;margin:0 0 20px;">
<a href="{reset_url}" style="color:#0052FF;text-decoration:underline;">{reset_url}</a>
</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:12px;border:1px solid #334155;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<p style="color:#F59E0B;font-size:12px;margin:0 0 4px;font-weight:600;">This link expires in 1 hour</p>
<p style="color:#64748B;font-size:12px;margin:0;">If you didn't request this, you can safely ignore this email.</p>
</td></tr>
</table>"""
    return _base_html(content)


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
<td style="padding:8px 12px;color:#ffffff;font-size:13px;border-bottom:1px solid #334155;">{spike.get('symbol','?')}</td>
<td style="padding:8px 12px;color:#F87171;font-size:13px;border-bottom:1px solid #334155;">{spike.get('confidence','?')}%</td>
<td style="padding:8px 12px;color:#94A3B8;font-size:13px;border-bottom:1px solid #334155;">{spike.get('date','?')}</td>
</tr>"""

    details_table = ""
    if detail_rows:
        details_table = f"""
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:12px;border:1px solid #334155;margin-bottom:20px;border-collapse:collapse;">
<tr>
<th style="padding:10px 12px;color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px;text-align:left;border-bottom:1px solid #334155;">Ticker</th>
<th style="padding:10px 12px;color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px;text-align:left;border-bottom:1px solid #334155;">Confidence</th>
<th style="padding:10px 12px;color:#64748B;font-size:11px;text-transform:uppercase;letter-spacing:1px;text-align:left;border-bottom:1px solid #334155;">Date</th>
</tr>
{detail_rows}
</table>"""

    content = f"""
<h2 style="color:#F87171;font-size:20px;margin:0 0 8px;font-weight:600;">Toxic Spikes Detected</h2>
<p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0 0 20px;">
The nightly memory cleanup found <strong style="color:#F87171;">{toxic_count} high-confidence failures</strong> in the AI prediction engine. These have been re-tagged as negative lessons so the AI avoids repeating these mistakes.
</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0F172A;border-radius:12px;border:1px solid #334155;margin-bottom:20px;">
<tr><td style="padding:16px 20px;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr>
<td style="width:33%;text-align:center;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;">Toxic Removed</p>
<p style="color:#F87171;font-size:22px;margin:0;font-weight:700;">{toxic_count}</p>
</td>
<td style="width:33%;text-align:center;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;">Obsolete Pruned</p>
<p style="color:#FBBF24;font-size:22px;margin:0;font-weight:700;">{obsolete_count}</p>
</td>
<td style="width:33%;text-align:center;">
<p style="color:#64748B;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;">Episodes Now</p>
<p style="color:#10B981;font-size:22px;margin:0;font-weight:700;">{total_after}</p>
</td>
</tr>
</table>
</td></tr>
</table>
{details_table}
<p style="color:#94A3B8;font-size:13px;line-height:1.6;margin:0 0 24px;">
These toxic patterns are preserved in ChromaDB as <strong style="color:#FBBF24;">negative lessons</strong> &mdash; the AI will use them to avoid similar high-confidence errors in the future.
</p>
<table cellpadding="0" cellspacing="0" style="margin:0 auto;">
<tr><td style="background-color:#0052FF;border-radius:10px;padding:12px 28px;">
<a href="{APP_URL}" style="color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">View Memory Dashboard</a>
</td></tr>
</table>"""
    return _base_html(content)


async def send_toxic_spikes_email(
    recipient_email: str,
    toxic_count: int,
    obsolete_count: int,
    total_before: int,
    total_after: int,
    spike_details: list = None,
):
    """Send toxic spikes alert email after nightly cleanup detects bad predictions."""
    if not email_router.providers:
        logger.info(f"Email skipped (no providers configured): toxic spikes alert to {recipient_email}")
        return False
    try:
        params = {
            "from": SENDER_EMAIL,
            "to": [recipient_email],
            "subject": f"[{APP_NAME}] Toxic Spikes Alert — {toxic_count} High-Confidence Failures Detected",
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


# ── Waitlist Emails ──

def _war_room_invite_html(name: str, beta_key: str, rank: int, referral_count: int) -> str:
    """HTML email for War Room beta invite — the user has been bumped to the front."""
    display_name = name or "Trader"
    referral_line = ""
    if referral_count > 0:
        referral_line = f"""
<tr><td style="padding:12px 16px;background-color:#1a1a3e;border-radius:8px;margin-bottom:16px;">
<p style="color:#a78bfa;font-size:13px;margin:0;"><strong>{referral_count} referral{'s' if referral_count != 1 else ''}</strong> helped you skip the line</p>
</td></tr>
<tr><td style="height:12px;"></td></tr>"""

    content = f"""
<h2 style="color:#ffffff;font-size:22px;margin:0 0 4px;font-weight:700;">You've been bumped to the front.</h2>
<p style="color:#3DE8D9;font-size:14px;margin:0 0 20px;font-weight:600;">Welcome to the War Room, {display_name}.</p>
<p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0 0 24px;">
You were <strong style="color:#fff;">#{rank}</strong> in priority. Our adversarial AI system — where the 
<span style="color:#3DE8D9;">Strategist</span> generates signals and the 
<span style="color:#f97316;">Auditor</span> kills the bad ones — is now unlocked for you.
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
{referral_line}
<tr><td style="padding:20px;background-color:#0f172a;border:1px solid #334155;border-radius:12px;text-align:center;">
<p style="color:#64748B;font-size:11px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px;">Your Beta Access Key</p>
<p style="color:#3DE8D9;font-size:28px;font-weight:800;margin:0;font-family:monospace;letter-spacing:3px;">{beta_key}</p>
</td></tr>
</table>

<p style="color:#94A3B8;font-size:13px;line-height:1.6;margin:0 0 24px;">
Use this key to activate your beta account. As an early tester, every trade, prediction, and signal you interact with 
<strong style="color:#fff;">feeds our AI pipeline</strong> — making the system smarter for everyone.
</p>

<table width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center">
<a href="{APP_URL}" style="display:inline-block;background:linear-gradient(135deg,#14B8A6,#06B6D4);color:#ffffff;font-size:14px;font-weight:600;padding:14px 32px;border-radius:12px;text-decoration:none;">
Enter the War Room &rarr;
</a>
</td></tr>
</table>

<p style="color:#475569;font-size:11px;margin:24px 0 0;text-align:center;">
This key is unique to you. Do not share it. It expires in 7 days.
</p>
"""
    return _base_html(content)


def _referral_success_html(name: str, new_rank: int, referral_count: int, spots_skipped: int) -> str:
    """HTML email when a referral successfully joins — the referrer skipped the line."""
    display_name = name or "Trader"
    content = f"""
<h2 style="color:#ffffff;font-size:20px;margin:0 0 4px;font-weight:700;">You just skipped the line.</h2>
<p style="color:#a78bfa;font-size:14px;margin:0 0 20px;font-weight:600;">{display_name}, your referral landed.</p>

<p style="color:#94A3B8;font-size:14px;line-height:1.6;margin:0 0 24px;">
Someone used your referral link and you just jumped <strong style="color:#3DE8D9;">20 spots</strong> closer to the front.
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
<tr>
<td width="33%" style="padding:12px;background-color:#0f172a;border:1px solid #334155;border-radius:12px 0 0 12px;text-align:center;">
<p style="color:#3DE8D9;font-size:24px;font-weight:800;margin:0;">#{new_rank}</p>
<p style="color:#64748B;font-size:10px;margin:4px 0 0;text-transform:uppercase;">New Rank</p>
</td>
<td width="33%" style="padding:12px;background-color:#0f172a;border-top:1px solid #334155;border-bottom:1px solid #334155;text-align:center;">
<p style="color:#a78bfa;font-size:24px;font-weight:800;margin:0;">{referral_count}</p>
<p style="color:#64748B;font-size:10px;margin:4px 0 0;text-transform:uppercase;">Referrals</p>
</td>
<td width="33%" style="padding:12px;background-color:#0f172a;border:1px solid #334155;border-radius:0 12px 12px 0;text-align:center;">
<p style="color:#f97316;font-size:24px;font-weight:800;margin:0;">{spots_skipped}</p>
<p style="color:#64748B;font-size:10px;margin:4px 0 0;text-transform:uppercase;">Spots Skipped</p>
</td>
</tr>
</table>

<p style="color:#94A3B8;font-size:13px;line-height:1.6;margin:0 0 20px;">
Keep sharing. The top 100 in the priority queue become <strong style="color:#fff;">Founding Members</strong> — 
lifetime perks, exclusive badge, and first access to every new feature.
</p>

<table width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center">
<a href="{APP_URL}" style="display:inline-block;background:linear-gradient(135deg,#7C3AED,#6366F1);color:#ffffff;font-size:14px;font-weight:600;padding:12px 28px;border-radius:12px;text-decoration:none;">
Share Again &rarr;
</a>
</td></tr>
</table>
"""
    return _base_html(content)


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
