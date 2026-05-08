"""Merge the Red Slate `owner` account into `admin@risedual.ai`.

Keeps the Red Slate account as a deactivated shell for audit trail. All
user-owned resources (predictions, orders, credits, watchlists, chat history,
referrals, paper trades, broker connections) get re-pointed at the admin.

Safe to re-run: idempotent — nothing happens once the admin already has
role=owner and the Red Slate account is deactivated.
"""
from __future__ import annotations

import asyncio
import os
import logging
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("merge")

OWNER_EMAIL = "managingdirector@redslateholdings.com"
ADMIN_EMAIL = "admin@risedual.ai"

# Collections that hold MANY docs per user — safe to bulk re-point.
OWNERSHIP_FIELDS_MANY = [
    ("predictions",           "user_id"),
    ("credit_events",         "user_id"),
    ("chat_sessions",         "user_id"),
    ("smart_orders",          "user_id"),
    ("trading_bots",          "user_id"),
    ("paper_trades",          "user_id"),
    ("trades",                "user_id"),
    ("trade_ideas",           "user_id"),
    ("hypothesis_history",    "user_id"),
    ("journal_entries",       "user_id"),
    ("pnl_snapshots",         "user_id"),
    ("push_subscriptions",    "user_id"),
    ("api_keys",              "user_id"),
    ("broker_connections",    "user_id"),
    ("referral_hits",         "user_id"),
    ("referral_rewards",      "user_id"),
    ("watchlist_intelligence","user_id"),
    ("smart_money_scores",    "user_id"),
]

# One-per-user singleton collections — merge carefully so we don't create
# duplicate docs that would break the app. Strategy: if admin already has
# meaningful data, DELETE the Red Slate copy; otherwise re-point it.
OWNERSHIP_FIELDS_SINGLETON = [
    # (collection, field, "is-empty"-predicate applied to the Red Slate doc)
    ("paper_portfolios",  "user_id",
     lambda d: (not d.get("positions")) and float(d.get("cash") or 0) in (0, 100000, 100000.0)),
    ("user_credits",      "user_id", lambda d: int(d.get("balance") or 0) == 0),
    ("chat_memory_prefs", "user_id", lambda _: True),    # prefs — prefer admin's
    ("referral_codes",    "user_id", lambda _: False),   # never discard referral codes
    ("watchlists",        "user_id", lambda d: not d.get("symbols") and not d.get("items")),
]


async def main():
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    owner = await db.users.find_one({"email": OWNER_EMAIL})
    admin = await db.users.find_one({"email": ADMIN_EMAIL})
    if not owner:
        log.warning(f"{OWNER_EMAIL} not found — nothing to merge.")
        return
    if not admin:
        raise RuntimeError(f"{ADMIN_EMAIL} missing — cannot promote.")

    owner_id = str(owner["_id"])
    admin_id = str(admin["_id"])
    log.info(f"owner _id = {owner_id}")
    log.info(f"admin _id = {admin_id}")

    # ── Migrate ownership references ──
    moved_total = 0
    deleted_total = 0
    for coll_name, field in OWNERSHIP_FIELDS_MANY:
        coll = db[coll_name]
        try:
            result = await coll.update_many(
                {field: owner_id},
                {"$set": {field: admin_id}},
            )
            if result.modified_count:
                log.info(f"  migrated {coll_name}.{field} → {result.modified_count} docs")
                moved_total += result.modified_count
        except Exception as e:
            log.warning(f"  skip {coll_name}.{field}: {e}")

    # ── Handle singleton collections ──
    for coll_name, field, is_empty in OWNERSHIP_FIELDS_SINGLETON:
        coll = db[coll_name]
        try:
            rs_doc = await coll.find_one({field: owner_id})
            if not rs_doc:
                continue
            admin_doc = await coll.find_one({field: admin_id})
            if admin_doc and is_empty(rs_doc):
                # Admin already has a populated record; the Red Slate copy is
                # empty/default — safe to delete.
                await coll.delete_one({"_id": rs_doc["_id"]})
                log.info(f"  singleton {coll_name}: admin has active data, discarded empty Red Slate copy")
                deleted_total += 1
            elif admin_doc:
                # Both populated — rename Red Slate's to ...migrated so it doesn't
                # duplicate-key; keep admin's as canonical.
                await coll.update_one(
                    {"_id": rs_doc["_id"]},
                    {"$set": {field: f"_merged_{owner_id}"}},
                )
                log.warning(
                    f"  singleton {coll_name}: BOTH populated — admin kept canonical, "
                    f"Red Slate re-tagged '_merged_...' for manual review"
                )
            else:
                # Admin has no doc; re-point the Red Slate one.
                await coll.update_one({"_id": rs_doc["_id"]}, {"$set": {field: admin_id}})
                log.info(f"  singleton {coll_name}: re-pointed to admin (admin had none)")
                moved_total += 1
        except Exception as e:
            log.warning(f"  skip singleton {coll_name}.{field}: {e}")

    log.info(f"Total docs re-pointed: {moved_total}, discarded: {deleted_total}")

    # ── Promote admin ──
    if admin.get("role") != "owner":
        await db.users.update_one(
            {"_id": admin["_id"]},
            {"$set": {"role": "owner", "merged_from_email": OWNER_EMAIL,
                      "merged_at": datetime.now(timezone.utc).isoformat()}},
        )
        log.info(f"Promoted {ADMIN_EMAIL} → role=owner")
    else:
        log.info(f"{ADMIN_EMAIL} already has role=owner")

    # ── Deactivate old Red Slate shell ──
    if owner.get("role") != "merged":
        await db.users.update_one(
            {"_id": owner["_id"]},
            {"$set": {
                "role": "merged",
                "is_active": False,
                "subscription_status": "free",
                "merged_into_email": ADMIN_EMAIL,
                "merged_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        log.info(f"Deactivated {OWNER_EMAIL} (role=merged, is_active=false)")
    else:
        log.info(f"{OWNER_EMAIL} already merged")

    # ── Verify ──
    admin_after = await db.users.find_one({"email": ADMIN_EMAIL}, {"password_hash": 0})
    owner_after = await db.users.find_one({"email": OWNER_EMAIL}, {"password_hash": 0})
    log.info(f"admin now: role={admin_after.get('role')} active={admin_after.get('is_active', True)}")
    log.info(f"red slate now: role={owner_after.get('role')} active={owner_after.get('is_active')}")

    # Show any remaining docs pointing at old owner — should be zero for
    # collections we know about.
    log.info("Residual references at owner_id (should be 0):")
    for coll_name, field in OWNERSHIP_FIELDS_MANY:
        try:
            n = await db[coll_name].count_documents({field: owner_id})
            if n:
                log.warning(f"  {coll_name}.{field}: {n}")
        except Exception:
            pass
    for coll_name, field, _ in OWNERSHIP_FIELDS_SINGLETON:
        try:
            n = await db[coll_name].count_documents({field: owner_id})
            if n:
                log.warning(f"  {coll_name}.{field}: {n}")
        except Exception:
            pass

    log.info("Merge complete.")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
