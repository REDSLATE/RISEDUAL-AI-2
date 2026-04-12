"""Waitlist Service — Beta testing waitlist with referral-based priority scoring.

Priority Score: position - (referral_count * 20)
Lower score = higher priority. Active promoters jump the line.
Founding 100: Top 100 users selected after 30-day launch window.
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

db = None
_position_counter = None


def set_db(database):
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


async def join_waitlist(email: str, name: str = "", referred_by: str = "") -> Dict:
    """Add a user to the waitlist. Returns their position and referral code."""
    email = email.strip().lower()

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


async def get_waitlist_status(referral_code: str) -> Optional[Dict]:
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


async def get_waitlist_leaderboard(limit: int = 20) -> List[Dict]:
    """Get the top referrers on the waitlist."""
    cursor = db.waitlist.find(
        {"referral_count": {"$gt": 0}},
        {"_id": 0, "email": 0},
    ).sort("priority_score", 1).limit(limit)

    return await cursor.to_list(length=limit)


async def get_admin_waitlist(skip: int = 0, limit: int = 50, sort_by: str = "priority") -> Dict:
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


async def invite_users(count: int = 10) -> List[Dict]:
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


async def select_founding_100() -> List[Dict]:
    """Select the top 100 users as Founding Members based on priority score."""
    cursor = db.waitlist.find(
        {"status": {"$in": ["invited", "active"]}},
        {"_id": 0},
    ).sort("priority_score", 1).limit(100)

    founders = await cursor.to_list(length=100)
    codes = [f["referral_code"] for f in founders]

    if codes:
        await db.waitlist.update_many(
            {"referral_code": {"$in": codes}},
            {"$set": {"founding_member": True, "status": "founding"}},
        )

    logger.info(f"Selected {len(founders)} founding members")
    return founders


async def get_waitlist_stats() -> Dict:
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
