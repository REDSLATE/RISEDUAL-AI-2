"""Waitlist Service — Beta testing waitlist with referral-based priority scoring.

Priority Score: position - (referral_count * 20)
Lower score = higher priority. Active promoters jump the line.
Founding 100: Top 100 users selected after 30-day launch window.
"""
import uuid
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

db: Any = None
_position_counter = None


def set_db(database: Any) -> None:
    global db
    db = database


async def _get_next_position() -> int:
    """Atomically get the next waitlist position."""
    result = await db.waitlist_counter.find_one_and_update(
        {"_id": "position"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    return result["seq"]


def _generate_referral_code() -> str:
    """Generate a unique 8-char referral code."""
    return f"RD{uuid.uuid4().hex[:6].upper()}"


def _calculate_priority_score(position: int, referral_count: int) -> float:
    """Calculate weighted priority score. Lower = higher priority.
    
    Formula: position - (referral_count * 20)
    1 referral skips 20 spots in line.
    """
    return position - (referral_count * 20)


async def join_waitlist(email: str, name: str = "", referred_by: str = "") -> dict:
    """Add a user to the waitlist. Returns their position and referral code."""
    email = email.strip().lower()

    # Block admin/owner accounts from joining the waitlist
    admin_user = await db.users.find_one({"email": email, "role": {"$in": ["admin", "owner"]}})
    if admin_user:
        # Also remove if they were accidentally added before
        await db.waitlist.delete_many({"email": email})
        return {
            "already_joined": False,
            "blocked": True,
            "message": "This email is registered as an admin/owner account. Please log in directly.",
        }

    existing = await db.waitlist.find_one({"email": email}, {"_id": 0})
    if existing:
        return {
            "already_joined": True,
            "position": existing["position"],
            "referral_code": existing["referral_code"],
            "referral_count": existing.get("referral_count", 0),
            "priority_score": existing.get("priority_score", existing["position"]),
            "status": existing.get("status", "waiting"),
        }

    position = await _get_next_position()
    referral_code = _generate_referral_code()

    doc = {
        "email": email,
        "name": name.strip(),
        "position": position,
        "referral_code": referral_code,
        "referral_count": 0,
        "referred_by": referred_by.strip().upper() if referred_by else "",
        "priority_score": _calculate_priority_score(position, 0),
        "status": "waiting",  # waiting | invited | founding | active
        "signed_up_at": datetime.now(timezone.utc).isoformat(),
        "invited_at": None,
        "founding_member": False,
    }
    await db.waitlist.insert_one(doc)

    # Credit the referrer
    if referred_by:
        ref_code = referred_by.strip().upper()
        referrer = await db.waitlist.find_one({"referral_code": ref_code})
        if referrer:
            new_count = referrer.get("referral_count", 0) + 1
            new_score = _calculate_priority_score(referrer["position"], new_count)
            await db.waitlist.update_one(
                {"referral_code": ref_code},
                {"$inc": {"referral_count": 1}, "$set": {"priority_score": new_score}},
            )
            logger.info(f"Referral credited: {ref_code} now has {new_count} referrals (score: {new_score})")

            # Notify the referrer asynchronously
            import asyncio
            asyncio.create_task(notify_referral_success(ref_code))

    total = await db.waitlist.count_documents({})
    logger.info(f"Waitlist join: {email} at position #{position} (total: {total})")

    return {
        "already_joined": False,
        "position": position,
        "referral_code": referral_code,
        "referral_count": 0,
        "priority_score": doc["priority_score"],
        "status": "waiting",
        "total_waitlist": total,
    }


async def get_waitlist_status(referral_code: str) -> Optional[dict]:
    """Get a user's waitlist status by their referral code."""
    doc = await db.waitlist.find_one({"referral_code": referral_code.upper()}, {"_id": 0})
    if not doc:
        return None

    # Calculate current rank (how many people are ahead)
    ahead = await db.waitlist.count_documents({
        "priority_score": {"$lt": doc["priority_score"]},
        "status": "waiting",
    })
    total = await db.waitlist.count_documents({})

    return {
        "email": doc["email"],
        "name": doc.get("name", ""),
        "position": doc["position"],
        "referral_code": doc["referral_code"],
        "referral_count": doc.get("referral_count", 0),
        "priority_score": doc.get("priority_score", doc["position"]),
        "rank": ahead + 1,
        "total_waitlist": total,
        "status": doc.get("status", "waiting"),
        "founding_member": doc.get("founding_member", False),
        "signed_up_at": doc.get("signed_up_at", ""),
    }


async def get_waitlist_leaderboard(limit: int = 20) -> list[dict]:
    """Get the top referrers on the waitlist."""
    cursor = db.waitlist.find(
        {"referral_count": {"$gt": 0}},
        {"_id": 0, "email": 0},
    ).sort("priority_score", 1).limit(limit)

    return await cursor.to_list(length=limit)


async def get_admin_waitlist(skip: int = 0, limit: int = 50, sort_by: str = "priority") -> dict:
    """Admin view of the full waitlist with stats."""
    sort_field = "priority_score" if sort_by == "priority" else "position"

    cursor = db.waitlist.find(
        {}, {"_id": 0}
    ).sort(sort_field, 1).skip(skip).limit(limit)

    entries = await cursor.to_list(length=limit)
    total = await db.waitlist.count_documents({})
    invited = await db.waitlist.count_documents({"status": {"$in": ["invited", "founding", "active"]}})
    waiting = await db.waitlist.count_documents({"status": "waiting"})
    founding = await db.waitlist.count_documents({"founding_member": True})

    return {
        "entries": entries,
        "total": total,
        "invited": invited,
        "waiting": waiting,
        "founding_count": founding,
        "skip": skip,
        "limit": limit,
    }


async def invite_users(count: int = 10) -> list[dict]:
    """Invite the top N users from the waitlist based on priority score."""
    cursor = db.waitlist.find(
        {"status": "waiting"},
        {"_id": 0},
    ).sort("priority_score", 1).limit(count)

    to_invite = await cursor.to_list(length=count)
    invited = []

    for entry in to_invite:
        await db.waitlist.update_one(
            {"referral_code": entry["referral_code"]},
            {"$set": {
                "status": "invited",
                "invited_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        invited.append({
            "email": entry["email"],
            "name": entry.get("name", ""),
            "referral_code": entry["referral_code"],
            "position": entry["position"],
            "priority_score": entry.get("priority_score"),
            "referral_count": entry.get("referral_count", 0),
        })

    logger.info(f"Invited {len(invited)} users from waitlist")
    return invited


async def select_founding_50() -> list[dict]:
    """Select the top 50 users as Founding Members based on priority score."""
    cursor = db.waitlist.find(
        {"status": {"$in": ["invited", "active"]}},
        {"_id": 0},
    ).sort("priority_score", 1).limit(50)

    founders = await cursor.to_list(length=50)
    codes = [f["referral_code"] for f in founders]

    if codes:
        await db.waitlist.update_many(
            {"referral_code": {"$in": codes}},
            {"$set": {"founding_member": True, "status": "founding"}},
        )

    logger.info(f"Selected {len(founders)} founding members")
    return founders


async def get_waitlist_stats() -> dict:
    """Quick stats for the waitlist."""
    total = await db.waitlist.count_documents({})
    waiting = await db.waitlist.count_documents({"status": "waiting"})
    invited = await db.waitlist.count_documents({"status": {"$in": ["invited", "founding", "active"]}})
    founding = await db.waitlist.count_documents({"founding_member": True})
    top_referrer = await db.waitlist.find_one(
        {"referral_count": {"$gt": 0}},
        {"_id": 0, "name": 1, "referral_count": 1, "referral_code": 1},
        sort=[("referral_count", -1)],
    )

    return {
        "total": total,
        "waiting": waiting,
        "invited": invited,
        "founding": founding,
        "top_referrer": top_referrer,
    }


async def get_waitlist_analytics(days: int = 30) -> dict:
    """Comprehensive analytics for the waitlist admin dashboard."""
    from datetime import datetime, timezone, timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # ── Daily signups (time series) ──
    all_entries = await db.waitlist.find(
        {"signed_up_at": {"$gte": cutoff}},
        {"_id": 0, "signed_up_at": 1, "referred_by": 1, "status": 1, "referral_count": 1},
    ).to_list(length=10000)

    daily_map = {}
    for entry in all_entries:
        day = entry.get("signed_up_at", "")[:10]
        if not day:
            continue
        if day not in daily_map:
            daily_map[day] = {"date": day, "total": 0, "organic": 0, "referred": 0}
        daily_map[day]["total"] += 1
        if entry.get("referred_by"):
            daily_map[day]["referred"] += 1
        else:
            daily_map[day]["organic"] += 1

    daily_signups = sorted(daily_map.values(), key=lambda x: x["date"])

    # ── Status breakdown (funnel) ──
    total = await db.waitlist.count_documents({})
    waiting = await db.waitlist.count_documents({"status": "waiting"})
    invited = await db.waitlist.count_documents({"status": "invited"})
    active = await db.waitlist.count_documents({"status": "active"})
    founding = await db.waitlist.count_documents({"status": "founding"})

    # ── Referral metrics ──
    total_with_referral = await db.waitlist.count_documents({"referred_by": {"$ne": ""}})
    total_referrers = await db.waitlist.count_documents({"referral_count": {"$gt": 0}})

    referral_rate = round((total_with_referral / total * 100), 1) if total > 0 else 0
    invite_conversion = round((active / invited * 100), 1) if invited > 0 else 0

    # ── Top referrers ──
    top_referrers_cursor = db.waitlist.find(
        {"referral_count": {"$gt": 0}},
        {"_id": 0, "name": 1, "email": 1, "referral_code": 1, "referral_count": 1, "priority_score": 1, "status": 1},
    ).sort("referral_count", -1).limit(10)
    top_referrers = await top_referrers_cursor.to_list(length=10)
    for r in top_referrers:
        r["email"] = r["email"][:3] + "***" + r["email"][r["email"].index("@"):]

    # ── Invite timeline ──
    invited_entries = await db.waitlist.find(
        {"invited_at": {"$ne": None}},
        {"_id": 0, "invited_at": 1},
    ).to_list(length=10000)

    invite_daily: dict[str, int] = {}
    for entry in invited_entries:
        day = (entry.get("invited_at") or "")[:10]
        if day:
            invite_daily[day] = invite_daily.get(day, 0) + 1

    invite_timeline = [{"date": d, "invites": c} for d, c in sorted(invite_daily.items())]

    # ── Priority score distribution ──
    all_scores = await db.waitlist.find(
        {"status": "waiting"},
        {"_id": 0, "priority_score": 1},
    ).to_list(length=10000)
    scores = [e.get("priority_score", 0) for e in all_scores]

    buckets = {"< 0": 0, "0-20": 0, "21-50": 0, "51-100": 0, "> 100": 0}
    for s in scores:
        if s < 0:
            buckets["< 0"] += 1
        elif s <= 20:
            buckets["0-20"] += 1
        elif s <= 50:
            buckets["21-50"] += 1
        elif s <= 100:
            buckets["51-100"] += 1
        else:
            buckets["> 100"] += 1

    score_distribution = [{"range": k, "count": v} for k, v in buckets.items()]

    return {
        "period_days": days,
        "daily_signups": daily_signups,
        "funnel": {
            "total": total,
            "waiting": waiting,
            "invited": invited,
            "active": active,
            "founding": founding,
        },
        "referral_metrics": {
            "total_referred": total_with_referral,
            "total_organic": total - total_with_referral,
            "total_referrers": total_referrers,
            "referral_rate": referral_rate,
            "invite_conversion": invite_conversion,
        },
        "top_referrers": top_referrers,
        "invite_timeline": invite_timeline,
        "score_distribution": score_distribution,
    }



def _generate_beta_key() -> str:
    """Generate a secure, unique beta access key. Format: BETA-XXXX-XXXX-XXXX"""
    def seg() -> str:
        return secrets.token_hex(2).upper()
    return f"BETA-{seg()}-{seg()}-{seg()}"


async def auto_invite_top_users(batch_size: int = 5) -> list[dict]:
    """Scheduled task: Auto-invite the top users by priority score.
    
    Generates beta access keys, sends War Room invite emails, 
    and updates their status to 'invited'.
    """
    from services.email_service import send_war_room_invite

    cursor = db.waitlist.find(
        {"status": "waiting"},
        {"_id": 0},
    ).sort("priority_score", 1).limit(batch_size)

    to_invite = await cursor.to_list(length=batch_size)
    if not to_invite:
        logger.info("Auto-invite: No users waiting in queue")
        return []

    invited = []
    now = datetime.now(timezone.utc)

    for entry in to_invite:
        beta_key = _generate_beta_key()

        # Calculate rank
        ahead = await db.waitlist.count_documents({
            "priority_score": {"$lt": entry.get("priority_score", entry["position"])},
            "status": "waiting",
        })
        rank = ahead + 1

        # Update status and store beta key
        await db.waitlist.update_one(
            {"referral_code": entry["referral_code"]},
            {"$set": {
                "status": "invited",
                "invited_at": now.isoformat(),
                "beta_key": beta_key,
                "beta_key_expires": (now + timedelta(days=7)).isoformat(),
            }},
        )

        # Send War Room invite email
        email_sent = await send_war_room_invite(
            email=entry["email"],
            name=entry.get("name", ""),
            beta_key=beta_key,
            rank=rank,
            referral_count=entry.get("referral_count", 0),
        )

        invited.append({
            "email": entry["email"],
            "name": entry.get("name", ""),
            "referral_code": entry["referral_code"],
            "beta_key": beta_key,
            "rank": rank,
            "referral_count": entry.get("referral_count", 0),
            "email_sent": email_sent,
        })

    logger.info(f"Auto-invite: Invited {len(invited)} users from waitlist")
    return invited


async def notify_referral_success(referrer_code: str) -> None:
    """Send a referral success email to the referrer when someone joins via their link."""
    from services.email_service import send_referral_success, _is_configured

    if not _is_configured():
        return

    referrer = await db.waitlist.find_one({"referral_code": referrer_code}, {"_id": 0})
    if not referrer:
        return

    # Calculate current rank
    ahead = await db.waitlist.count_documents({
        "priority_score": {"$lt": referrer.get("priority_score", referrer["position"])},
        "status": "waiting",
    })
    rank = ahead + 1

    await send_referral_success(
        email=referrer["email"],
        name=referrer.get("name", ""),
        new_rank=rank,
        referral_count=referrer.get("referral_count", 0),
        spots_skipped=referrer.get("referral_count", 0) * 20,
    )
