"""Email notification service using Resend for referral events."""
import os
import asyncio
import logging
import resend
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / '.env')

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'onboarding@resend.dev')
APP_NAME = "RISEDUAL AI"
APP_URL = "https://risedual.ai"

resend.api_key = RESEND_API_KEY


def _is_configured():
    return RESEND_API_KEY and not RESEND_API_KEY.startswith('re_YOUR')


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
    if not _is_configured():
        logger.info(f"Email skipped (no API key): referral signup notification to {referrer_email}")
        return False
    try:
        params = {
            "from": SENDER_EMAIL,
            "to": [referrer_email],
            "subject": f"Your friend just joined {APP_NAME}!",
            "html": _referral_signup_html(referrer_name, referred_email),
        }
        result = await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Referral signup email sent to {referrer_email}, id: {result.get('id', 'unknown')}")
        return True
    except Exception as e:
        logger.error(f"Failed to send referral signup email to {referrer_email}: {e}")
        return False


async def send_reward_earned_email(referrer_email: str, referrer_name: str, referred_email: str):
    """Notify referrer when they earn a free month."""
    if not _is_configured():
        logger.info(f"Email skipped (no API key): reward earned notification to {referrer_email}")
        return False
    try:
        params = {
            "from": SENDER_EMAIL,
            "to": [referrer_email],
            "subject": f"You earned a free month of {APP_NAME} Pro!",
            "html": _reward_earned_html(referrer_name, referred_email),
        }
        result = await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Reward earned email sent to {referrer_email}, id: {result.get('id', 'unknown')}")
        return True
    except Exception as e:
        logger.error(f"Failed to send reward email to {referrer_email}: {e}")
        return False


async def send_welcome_referral_email(user_email: str, user_name: str, referrer_name: str):
    """Send welcome email to newly referred user."""
    if not _is_configured():
        logger.info(f"Email skipped (no API key): welcome email to {user_email}")
        return False
    try:
        params = {
            "from": SENDER_EMAIL,
            "to": [user_email],
            "subject": f"Welcome to {APP_NAME} — Your 7-Day Pro Trial is Active!",
            "html": _welcome_referral_html(user_name, referrer_name),
        }
        result = await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Welcome email sent to {user_email}, id: {result.get('id', 'unknown')}")
        return True
    except Exception as e:
        logger.error(f"Failed to send welcome email to {user_email}: {e}")
        return False
