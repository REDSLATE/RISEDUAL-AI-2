"""Failure Loop Service — trade idea memory and review layer.

Stores AI/user trade ideas, post-trade reviews with outcome tagging,
and builds failure pattern warnings surfaced in chat responses.

Reason tags: late_entry, weak_confirmation, overconfidence, high_volatility,
poor_risk_reward, trend_fade, news_risk
"""
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)

db = None

REASON_TAGS = [
    "late_entry",
    "weak_confirmation",
    "overconfidence",
    "high_volatility",
    "poor_risk_reward",
    "trend_fade",
    "news_risk",
]


def set_db(database: object) -> None:
    global db
    db = database


async def create_trade_idea(user_id: str, symbol: str, direction: str, thesis: str,
                            confidence: float, source: str = "ai", tags: list = None) -> dict:
    """Store a new trade idea."""
    if db is None:
        return {}

    idea = {
        "idea_id": str(uuid4()),
        "user_id": user_id,
        "symbol": symbol.upper(),
        "direction": direction,
        "thesis": thesis,
        "confidence": confidence,
        "source": source,
        "tags": tags or [],
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.trade_ideas.insert_one({**idea})
    await _log_event(user_id, "trade_idea_created", idea["idea_id"], idea)
    return idea


async def review_trade_outcome(user_id: str, idea_id: str, outcome: str, pnl: float = 0,
                               reason_tags: list = None, notes: str = "", approved_for_learning: bool = False) -> dict:
    """Review a trade outcome with tagging."""
    if db is None:
        return {}

    idea = await db.trade_ideas.find_one({"idea_id": idea_id, "user_id": user_id}, {"_id": 0})
    if not idea:
        raise ValueError("Unknown idea_id")

    update = {
        "status": outcome,
        "pnl": pnl,
        "reason_tags": reason_tags or [],
        "review_notes": notes,
        "approved_for_learning": approved_for_learning,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.trade_ideas.update_one(
        {"idea_id": idea_id, "user_id": user_id},
        {"$set": update}
    )

    await _log_event(user_id, "trade_reviewed", idea_id, {
        "outcome": outcome, "pnl": pnl,
        "reason_tags": reason_tags or [], "notes": notes,
        "approved_for_learning": approved_for_learning,
    })

    return {**idea, **update}


async def get_user_ideas(user_id: str, status: str = None, limit: int = 20) -> list:
    """Get user's trade ideas, optionally filtered by status."""
    if db is None:
        return []

    query = {"user_id": user_id}
    if status:
        query["status"] = status

    cursor = db.trade_ideas.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    return [doc async for doc in cursor]


async def get_user_timeline(user_id: str, limit: int = 30) -> list:
    """Get user's event timeline."""
    if db is None:
        return []

    cursor = db.failure_loop_events.find(
        {"user_id": user_id}, {"_id": 0}
    ).sort("timestamp", -1).limit(limit)
    return [doc async for doc in cursor]


async def summarize_failure_patterns(user_id: str) -> list:
    """Analyze approved losses/mixed outcomes to find recurring failure patterns."""
    if db is None:
        return []

    cursor = db.trade_ideas.find({
        "user_id": user_id,
        "status": {"$in": ["loss", "mixed"]},
        "approved_for_learning": True,
    }, {"_id": 0})

    tag_counts = Counter()
    tag_pnl = defaultdict(list)
    tag_symbols = defaultdict(set)

    async for idea in cursor:
        for tag in idea.get("reason_tags", []):
            tag_counts[tag] += 1
            tag_pnl[tag].append(float(idea.get("pnl", 0)))
            tag_symbols[tag].add(idea.get("symbol", ""))

    rows = []
    for tag, count in tag_counts.most_common():
        avg = round(sum(tag_pnl[tag]) / len(tag_pnl[tag]), 2) if tag_pnl[tag] else 0
        rows.append({
            "tag": tag,
            "count": count,
            "avg_pnl": avg,
            "symbols": sorted(tag_symbols[tag]),
        })
    return rows


async def build_memory_warnings(user_id: str) -> list:
    """Build warning strings from reviewed failure patterns for chat injection."""
    patterns = await summarize_failure_patterns(user_id)
    warnings = []
    for row in patterns[:5]:
        warnings.append(
            f"Past reviewed trades flagged '{row['tag']}' {row['count']} times with average P&L {row['avg_pnl']}."
        )
    return warnings


async def _log_event(user_id: str, event_type: str, idea_id: str, payload: dict) -> None:
    """Log a failure loop event."""
    if db is None:
        return
    await db.failure_loop_events.insert_one({
        "user_id": user_id,
        "type": event_type,
        "idea_id": idea_id,
        "payload": payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
