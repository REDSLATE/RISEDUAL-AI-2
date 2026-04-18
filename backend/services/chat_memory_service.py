"""Chat Memory Service — Persistent memory for AI chat across sessions.

Automatically extracts key facts, preferences, and trading context from conversations.
Injects relevant memories into future chat prompts for continuity.
"""
import os
import logging
from datetime import datetime, timezone
from typing import List, Dict

logger = logging.getLogger(__name__)

db = None

def set_db(database):
    global db
    db = database


async def get_memory_enabled(user_id: str) -> bool:
    """Check if a user has chat memory enabled."""
    pref = await db.chat_memory_prefs.find_one({"user_id": user_id}, {"_id": 0})
    if not pref:
        return True  # Enabled by default for Pro users
    return pref.get("enabled", True)


async def set_memory_enabled(user_id: str, enabled: bool):
    """Toggle chat memory on/off for a user."""
    await db.chat_memory_prefs.update_one(
        {"user_id": user_id},
        {"$set": {"enabled": enabled, "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )


async def get_memories(user_id: str, limit: int = 50) -> List[Dict]:
    """Get all stored memories for a user."""
    cursor = db.chat_memories.find(
        {"user_id": user_id},
        {"_id": 0}
    ).sort("created_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def delete_memory(user_id: str, memory_id: str) -> bool:
    """Delete a specific memory."""
    result = await db.chat_memories.delete_one({"user_id": user_id, "memory_id": memory_id})
    return result.deleted_count > 0


async def clear_all_memories(user_id: str) -> int:
    """Clear all memories for a user."""
    result = await db.chat_memories.delete_many({"user_id": user_id})
    return result.deleted_count


async def save_memory(user_id: str, content: str, category: str = "general", source_session: str = ""):
    """Save a single memory entry."""
    import uuid
    memory_id = f"mem_{uuid.uuid4().hex[:12]}"
    doc = {
        "user_id": user_id,
        "memory_id": memory_id,
        "content": content,
        "category": category,
        "source_session": source_session,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.chat_memories.insert_one(doc)
    return memory_id


async def extract_memories_from_conversation(user_id: str, messages: List[Dict], session_id: str):
    """Use AI to extract key facts/preferences from a conversation and store them.
    
    Called after each chat exchange to build persistent memory.
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return

    # Only process if there are enough messages to extract from
    if len(messages) < 2:
        return

    # Get the last user message and AI response
    recent = messages[-4:] if len(messages) >= 4 else messages
    convo_text = "\n".join([f"{'User' if m.get('role') == 'user' else 'AI'}: {m.get('content', '')[:500]}" for m in recent])

    # Get existing memories to avoid duplicates
    existing = await get_memories(user_id, limit=20)
    existing_text = "\n".join([f"- {m['content']}" for m in existing]) if existing else "None yet."

    prompt = f"""Analyze this conversation and extract ONLY new, important facts worth remembering for future conversations.

EXISTING MEMORIES (do NOT repeat these):
{existing_text}

RECENT CONVERSATION:
{convo_text}

Extract key facts like:
- Trading preferences (e.g., "prefers swing trading", "focuses on tech stocks")
- Specific positions or watchlist items mentioned
- Risk tolerance indicators
- Investment goals or timeline
- Market opinions or biases

Return ONLY a JSON array of strings. Each string is one memory fact.
Return empty array [] if nothing new worth remembering.
Example: ["Bullish on NVDA for Q2 earnings", "Prefers options over equities", "Risk tolerance: moderate"]
Return ONLY the JSON array, no other text."""

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(
            api_key=api_key,
            session_id=f"mem_extract_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            system_message="You extract key facts from trading conversations. Return only JSON arrays."
        ).with_model("openai", "gpt-5.2")

        response = await chat.send_message(UserMessage(text=prompt))
        text = str(response).strip()

        # Parse the JSON array
        import json
        bracket_start = text.find('[')
        bracket_end = text.rfind(']')
        if bracket_start >= 0 and bracket_end > bracket_start:
            facts = json.loads(text[bracket_start:bracket_end + 1])
            for fact in facts[:5]:  # Max 5 new memories per extraction
                if isinstance(fact, str) and len(fact) > 5:
                    await save_memory(user_id, fact.strip(), "auto", session_id)
            if facts:
                logger.info(f"Extracted {len(facts)} memories for user {user_id}")
    except Exception as e:
        logger.warning(f"Memory extraction failed: {e}")


async def get_memory_context(user_id: str) -> str:
    """Build a memory context string to inject into AI prompts."""
    memories = await get_memories(user_id, limit=15)
    if not memories:
        return ""

    lines = ["PERSISTENT MEMORY — Things you remember about this user from past conversations:"]
    for m in memories:
        lines.append(f"- {m['content']}")
    lines.append("\nUse these memories naturally in your responses. Reference past discussions when relevant. Do NOT list memories explicitly unless asked.")
    return "\n".join(lines)
