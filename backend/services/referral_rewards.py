"""Referral reward granting — converts share hits into Pro/credit entitlements.

Reward tiers:

Per-share threshold (continuous, any time of month)
  - 5 unique visitor hits                        → 7-day Pro trial

Monthly leaderboard (granted on the 1st of each month for prior month)
  - #1: 30-day Pro Max trial
  - #2, #3: 30-day Pro trial
  - #4, #5: 100 credits each

Anti-gaming rules:
 - Each tier grants at most once per user per calendar month
 - Trial extensions stack (extend from current trial_expires_at if already trialing)
 - Users with an active paid Pro/Pro Max subscription are skipped for trial grants
 - Won monthly rewards are logged in ``referral_rewards`` for audit
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

THRESHOLD_5_HITS_DAYS = 7
THRESHOLD_HITS = 5
MONTHLY_PRO_MAX_DAYS = 30
MONTHLY_PRO_DAYS = 30
MONTHLY_CREDITS_REWARD = 100

# {rank: (tier_name, kind, amount)}  kind = 'trial_pro_max' | 'trial_pro' | 'credits'
MONTHLY_TIERS = {
    1: ("monthly_winner", "trial_pro_max", MONTHLY_PRO_MAX_DAYS),
    2: ("monthly_runner_up", "trial_pro", MONTHLY_PRO_DAYS),
    3: ("monthly_runner_up", "trial_pro", MONTHLY_PRO_DAYS),
    4: ("monthly_finalist", "credits", MONTHLY_CREDITS_REWARD),
    5: ("monthly_finalist", "credits", MONTHLY_CREDITS_REWARD),
}


def _ref_for_user(user: dict) -> str | None:
    """Reconstruct the user's share ref exactly as the frontend builds it."""
    identifier = user.get("id") or user.get("_id") or user.get("email") or ""
    raw_id = re.sub(r"[^a-zA-Z0-9]", "", str(identifier))[-8:]
    return f"share-u{raw_id}" if raw_id else None


async def _find_user_by_ref(db: Any, ref: str) -> dict | None:
    """Given a ``share-u{suffix}`` code, find the matching user."""
    m = re.match(r"^share-u([a-zA-Z0-9]+)$", ref or "")
    if not m:
        return None
    async for user in db.users.find({}, {"password": 0}):
        if _ref_for_user(user) == ref:
            return user
    return None


async def _grant_trial(db: Any, user: dict, days: int, plan: str, reason: str) -> dict:
    """Grant or extend a Pro/Pro Max trial. Skips users on a paid plan at or
    above the offered plan tier.
    """
    current_status = user.get("subscription_status")
    # Don't downgrade — skip if user is already on equal/higher paid plan
    if current_status == "pro_max":
        return {"granted": False, "reason": "user_already_pro_max"}
    if current_status == "pro" and plan == "pro":
        return {"granted": False, "reason": "user_already_pro"}

    now = datetime.now(timezone.utc)
    current_expires = user.get("trial_expires_at")
    if isinstance(current_expires, str):
        try:
            current_expires = datetime.fromisoformat(current_expires.replace("Z", "+00:00"))
        except ValueError:
            current_expires = None

    base = current_expires if (current_expires and current_expires > now) else now
    new_expires = base + timedelta(days=days)

    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "subscription_status": "trial",
            "trial_plan": plan,  # "pro" or "pro_max"
            "trial_expires_at": new_expires.isoformat(),
            "last_referral_reward_at": now.isoformat(),
            "last_referral_reward_reason": reason,
        }},
    )
    return {"granted": True, "new_expires_at": new_expires.isoformat(), "reason": reason, "plan": plan}


async def _grant_credits(db: Any, user: dict, amount: int, reason: str) -> dict:
    """Credit the user's wallet. No subscription change."""
    now = datetime.now(timezone.utc)
    await db.user_credits.update_one(
        {"user_id": str(user["_id"])},
        {
            "$inc": {"credits": amount, "total_earned": amount},
            "$set": {"last_referral_reward_at": now.isoformat()},
            "$setOnInsert": {
                "plan_key": user.get("subscription_status", "free"),
                "total_spent": 0,
                "created_at": now.isoformat(),
            },
        },
        upsert=True,
    )
    # Credit-log event
    try:
        await db.credit_events.insert_one({
            "user_id": str(user["_id"]),
            "action": "referral_reward",
            "credits_charged": -amount,  # negative because it's a grant
            "was_unlimited": False,
            "description": f"Referral reward ({reason}) — {amount} credits",
            "timestamp": now.isoformat(),
        })
    except Exception:
        pass
    return {"granted": True, "credits": amount, "reason": reason}


async def _already_rewarded(db: Any, user_id: Any, tier: str, period: str) -> bool:
    existing = await db.referral_rewards.find_one({
        "user_id": user_id, "tier": tier, "period": period,
    })
    return existing is not None


async def _record_reward(db: Any, user: dict, tier: str, period: str, kind: str, amount: int, detail: dict) -> None:
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": user["_id"],
        "user_email": user.get("email"),
        "tier": tier,
        "period": period,
        "kind": kind,  # trial_pro_max | trial_pro | credits
        "amount": amount,
        "granted_at": now.isoformat(),
        **detail,
    }
    await db.referral_rewards.insert_one(doc)


async def _fire_reward_notification(db: Any, user: dict, title: str, body: str) -> None:
    try:
        from services.push_service import send_to_user
        await send_to_user(db, user_id=str(user["_id"]), title=title, body=body, url="/#dashboard")
    except Exception as e:
        logger.debug(f"Reward notify error: {e}")


async def _fire_reward_email(
    user: dict,
    tier: str,
    kind: str,
    amount: int,
    hits: int,
    rank: int | None = None,
    period: str | None = None,
) -> None:
    """Send tiered reward email (non-blocking best-effort)."""
    email = (user.get("email") or "").strip()
    if not email:
        return
    try:
        from services.email_service import send_tiered_reward_email
        name = user.get("name") or email.split("@")[0]
        sent = await send_tiered_reward_email(
            user_email=email,
            user_name=name,
            tier=tier,
            kind=kind,
            amount=amount,
            hits=hits,
            rank=rank,
            period=period,
        )
        if sent:
            logger.info(f"Reward email sent: {email} tier={tier} kind={kind} amount={amount}")
        else:
            logger.info(f"Reward email not sent (no providers/skip): {email} tier={tier}")
    except Exception as e:
        logger.warning(f"Reward email error for {email}: {e}")


async def scan_hit_threshold_rewards(db: Any) -> dict:
    """Grant 7-day Pro to every user whose share-hit count crosses THRESHOLD_HITS
    this calendar month, at most once per user per month.
    """
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period = start_of_month.strftime("%Y-%m")

    pipeline = [
        {"$match": {"timestamp": {"$gte": start_of_month.isoformat()}}},
        {"$group": {"_id": "$ref", "hits": {"$sum": 1}}},
        {"$match": {"hits": {"$gte": THRESHOLD_HITS}}},
    ]
    results = await db.referral_hits.aggregate(pipeline).to_list(1000)

    granted = 0
    skipped = 0
    for r in results:
        ref = r["_id"]
        hits = r["hits"]
        user = await _find_user_by_ref(db, ref)
        if not user:
            skipped += 1
            continue
        if await _already_rewarded(db, user["_id"], "hits_threshold", period):
            skipped += 1
            continue
        res = await _grant_trial(db, user, THRESHOLD_5_HITS_DAYS, plan="pro", reason="hits_threshold")
        if not res["granted"]:
            skipped += 1
            continue
        await _record_reward(db, user, "hits_threshold", period, "trial_pro", THRESHOLD_5_HITS_DAYS,
                             {"hits_at_grant": hits, "trial_expires_at": res["new_expires_at"]})
        await _fire_reward_notification(
            db, user,
            title=f"🏆 You earned {THRESHOLD_5_HITS_DAYS} days of Pro",
            body=f"Your Smart Money Board reached {hits} scans this month. Keep sharing — monthly #1 wins Pro Max!",
        )
        await _fire_reward_email(
            user, tier="hits_threshold", kind="trial_pro",
            amount=THRESHOLD_5_HITS_DAYS, hits=hits, period=period,
        )
        granted += 1
        logger.info(f"Reward(hits): {user.get('email')} @ {hits} hits → 7d Pro")
    return {"tier": "hits_threshold", "eligible": len(results), "granted": granted, "skipped": skipped}


async def scan_monthly_leaderboard_rewards(db: Any) -> dict:
    """Run on the 1st of each month for the prior month. Ranks top 5 refs and
    grants rank-specific rewards (Pro Max for #1, Pro for #2-3, 100 credits for #4-5).
    """
    now = datetime.now(timezone.utc)
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_of_last_month = (first_of_this_month - timedelta(days=1)).replace(day=1)
    period = first_of_last_month.strftime("%Y-%m")

    pipeline = [
        {"$match": {
            "timestamp": {
                "$gte": first_of_last_month.isoformat(),
                "$lt": first_of_this_month.isoformat(),
            },
        }},
        {"$group": {"_id": "$ref", "hits": {"$sum": 1}}},
        {"$sort": {"hits": -1}},
        {"$limit": 5},
    ]
    ranked = await db.referral_hits.aggregate(pipeline).to_list(5)
    if not ranked:
        return {"period": period, "granted": [], "reason": "no_qualifying_refs"}

    granted_list = []
    for idx, r in enumerate(ranked, start=1):
        tier_name, kind, amount = MONTHLY_TIERS.get(idx, (None, None, None))
        if not tier_name:
            continue
        ref = r["_id"]
        hits = r["hits"]
        user = await _find_user_by_ref(db, ref)
        if not user:
            continue
        if await _already_rewarded(db, user["_id"], tier_name, period):
            continue

        if kind == "trial_pro_max":
            res = await _grant_trial(db, user, amount, plan="pro_max", reason=tier_name)
            if not res["granted"]:
                continue
            title = "👑 You won the Smart Money Leaderboard"
            body = f"{amount} days of Pro Max — on the house. You drove {hits} scans to RiseDual last month."
            await _record_reward(db, user, tier_name, period, kind, amount,
                                 {"rank": idx, "hits_at_grant": hits, "trial_expires_at": res["new_expires_at"]})
        elif kind == "trial_pro":
            res = await _grant_trial(db, user, amount, plan="pro", reason=tier_name)
            if not res["granted"]:
                continue
            title = f"🥈 #{idx} on last month's leaderboard"
            body = f"{amount} days of Pro on us. You drove {hits} scans to RiseDual last month."
            await _record_reward(db, user, tier_name, period, kind, amount,
                                 {"rank": idx, "hits_at_grant": hits, "trial_expires_at": res["new_expires_at"]})
        elif kind == "credits":
            res = await _grant_credits(db, user, amount, reason=tier_name)
            title = f"🎖 #{idx} on last month's leaderboard"
            body = f"{amount} RiseDual credits added to your wallet. Keep sharing — Pro rewards start at #3."
            await _record_reward(db, user, tier_name, period, kind, amount,
                                 {"rank": idx, "hits_at_grant": hits})
        else:
            continue

        await _fire_reward_notification(db, user, title=title, body=body)
        await _fire_reward_email(
            user, tier=tier_name, kind=kind, amount=amount, hits=hits,
            rank=idx, period=period,
        )
        granted_list.append({
            "rank": idx,
            "user": user.get("email"),
            "tier": tier_name,
            "kind": kind,
            "amount": amount,
            "hits": hits,
        })
        logger.info(f"Reward(rank #{idx}): {user.get('email')} → {kind} {amount} ({hits} hits, period {period})")

    return {"period": period, "granted": granted_list, "count": len(granted_list)}
