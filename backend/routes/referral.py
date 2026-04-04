"""Referral routes: referral code management, tracking, and rewards."""
import secrets
import string
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request
from routes.auth import get_current_user

router = APIRouter(prefix="/api/referral")

db = None
REWARD_CAP = 12  # Max rewards per 12-month rolling window
TRIAL_DAYS = 7

def set_db(database):
    global db
    db = database


def _generate_code(length=8):
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


@router.get("/info")
async def get_referral_info(request: Request):
    """Get user's referral code, link, and reward stats."""
    user = await get_current_user(request)
    user_id = user["_id"]

    # Get or create referral code
    ref_doc = await db.referral_codes.find_one({"user_id": user_id}, {"_id": 0})
    if not ref_doc:
        code = _generate_code()
        while await db.referral_codes.find_one({"code": code}):
            code = _generate_code()
        ref_doc = {
            "user_id": user_id,
            "code": code,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.referral_codes.insert_one(ref_doc)
        ref_doc.pop("_id", None)

    code = ref_doc["code"]

    # Count completed referrals
    total_referrals = await db.referrals.count_documents({"referrer_id": user_id})
    completed_referrals = await db.referrals.count_documents({"referrer_id": user_id, "status": "completed"})

    # Rolling 12-month reward count
    twelve_months_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    rewards_this_year = await db.referrals.count_documents({
        "referrer_id": user_id,
        "status": "completed",
        "reward_granted": True,
        "completed_at": {"$gte": twelve_months_ago},
    })

    # Get recent referrals
    cursor = db.referrals.find(
        {"referrer_id": user_id}, {"_id": 0, "referrer_id": 0}
    ).sort("created_at", -1).limit(20)
    referrals = []
    async for doc in cursor:
        referrals.append(doc)

    return {
        "code": code,
        "total_referrals": total_referrals,
        "completed_referrals": completed_referrals,
        "rewards_earned": rewards_this_year,
        "rewards_remaining": max(0, REWARD_CAP - rewards_this_year),
        "reward_cap": REWARD_CAP,
        "referrals": referrals,
    }


@router.get("/validate/{code}")
async def validate_referral_code(code: str):
    """Check if a referral code is valid (used during registration)."""
    ref = await db.referral_codes.find_one({"code": code.upper()}, {"_id": 0})
    if not ref:
        return {"valid": False}
    return {"valid": True, "code": ref["code"]}


async def process_referral_signup(referred_user_id: str, referred_email: str, ref_code: str):
    """Called after registration when a ref code is provided.
    Sets the new user on a 7-day Pro trial and creates a pending referral."""
    ref_doc = await db.referral_codes.find_one({"code": ref_code.upper()})
    if not ref_doc:
        return

    referrer_id = ref_doc["user_id"]

    # Don't allow self-referral
    if referrer_id == referred_user_id:
        return

    # Check if already referred
    existing = await db.referrals.find_one({"referred_id": referred_user_id})
    if existing:
        return

    # Create pending referral
    await db.referrals.insert_one({
        "referrer_id": referrer_id,
        "referred_id": referred_user_id,
        "referred_email": referred_email,
        "status": "pending",
        "reward_granted": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    })

    # Grant 7-day Pro trial to referred user
    trial_end = (datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)).isoformat()
    await db.users.update_one(
        {"_id": referred_user_id} if not isinstance(referred_user_id, str) else {"email": referred_email},
        {"$set": {
            "subscription_status": "trial",
            "trial_ends_at": trial_end,
            "referred_by": referrer_id,
        }}
    )
    logging.info(f"Referral: {referred_email} signed up via code {ref_code}, 7-day trial granted")


async def complete_referral_reward(referred_user_id: str):
    """Called when a referred user subscribes to Pro.
    Grants the referrer a reward month if under the 12/12 cap."""
    referral = await db.referrals.find_one({"referred_id": referred_user_id, "status": "pending"})
    if not referral:
        return

    referrer_id = referral["referrer_id"]

    # Check 12-month rolling cap
    twelve_months_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    rewards_count = await db.referrals.count_documents({
        "referrer_id": referrer_id,
        "status": "completed",
        "reward_granted": True,
        "completed_at": {"$gte": twelve_months_ago},
    })

    reward_granted = rewards_count < REWARD_CAP

    await db.referrals.update_one(
        {"_id": referral["_id"]},
        {"$set": {
            "status": "completed",
            "reward_granted": reward_granted,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }}
    )

    if reward_granted:
        # Add reward month to referrer's account
        await db.referral_rewards.insert_one({
            "user_id": referrer_id,
            "type": "free_month",
            "from_referral": str(referral["_id"]),
            "referred_email": referral["referred_email"],
            "granted_at": datetime.now(timezone.utc).isoformat(),
            "redeemed": False,
        })
        logging.info(f"Referral reward granted to {referrer_id} for {referral['referred_email']}")

    return reward_granted
