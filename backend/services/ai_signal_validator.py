"""AI Signal Validator — Adversarial AI layer that filters weak scanner signals.

Uses GPT-5.2 to evaluate each scanner match with:
- Signal strength confidence (0-100)
- Bull/bear/neutral verdict
- Risk assessment
- Recommended action
"""
import logging
import os
import json
from datetime import datetime, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)

_db = None

def set_db(database):
    global _db
    _db = database


async def validate_signals(matches: List[Dict], strategy_name: str = "") -> List[Dict]:
    """Validate a batch of scanner matches using AI. Returns enriched matches with AI scores."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key or not matches:
        return matches

    # Build a batch prompt for efficiency (validate up to 10 at once)
    batch = matches[:10]
    symbols_data = []
    for m in batch:
        symbols_data.append(
            f"- {m['symbol']}: ${m.get('price', '?')} | RSI={m.get('rsi', '?')} | "
            f"Vol={m.get('vol_ratio', m.get('volume_ratio', '?'))}x | Trend={m.get('trend', '?')} | "
            f"Signal: {m.get('detail', strategy_name)}"
        )

    prompt = f"""You are an adversarial AI signal validator for a trading platform. Your job is to critically evaluate scanner signals and filter out weak ones.

STRATEGY: {strategy_name}
MATCHES TO VALIDATE:
{chr(10).join(symbols_data)}

For each symbol, provide a JSON array with objects containing:
- "symbol": ticker
- "ai_confidence": 0-100 (how confident the signal is actionable)
- "verdict": "strong_buy" | "buy" | "hold" | "sell" | "strong_sell" | "avoid"
- "risk_level": "low" | "medium" | "high" | "extreme"
- "reasoning": one-sentence explanation (max 80 chars)
- "recommended_action": brief action suggestion (max 60 chars)

Be CRITICAL. Many scanner signals are noise. Only rate >70 confidence if the setup has multiple confirming factors.
Respond with ONLY the JSON array, no other text."""

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        session_id = f"validate_{datetime.now(timezone.utc).strftime('%H%M%S')}"
        chat = LlmChat(
            api_key=api_key, session_id=session_id,
            system_message="You are an expert quantitative analyst. Return only valid JSON arrays."
        ).with_model("openai", "gpt-5.2")

        response = await chat.send_message(UserMessage(text=prompt))
        response_text = str(response).strip()

        # Parse JSON array
        bracket_start = response_text.find("[")
        bracket_end = response_text.rfind("]")
        if bracket_start >= 0 and bracket_end > bracket_start:
            response_text = response_text[bracket_start:bracket_end + 1]
        ai_results = json.loads(response_text)

        # Merge AI results back into matches
        ai_map = {r["symbol"]: r for r in ai_results if isinstance(r, dict) and "symbol" in r}
        for m in batch:
            ai = ai_map.get(m["symbol"], {})
            m["ai_confidence"] = ai.get("ai_confidence", 50)
            m["ai_verdict"] = ai.get("verdict", "hold")
            m["ai_risk"] = ai.get("risk_level", "medium")
            m["ai_reasoning"] = ai.get("reasoning", "")
            m["ai_action"] = ai.get("recommended_action", "")
            m["ai_validated"] = True

        # Log validation
        if _db is not None:
            await _db.ai_validations.insert_one({
                "strategy": strategy_name,
                "symbols_validated": len(batch),
                "avg_confidence": round(sum(m.get("ai_confidence", 50) for m in batch) / len(batch), 1),
                "validated_at": datetime.now(timezone.utc).isoformat(),
            })

    except Exception as e:
        logger.error(f"AI validation failed: {e}")
        for m in batch:
            m["ai_confidence"] = None
            m["ai_validated"] = False
            m["ai_reasoning"] = "Validation unavailable"

    # Remaining matches beyond batch get no validation
    for m in matches[10:]:
        m["ai_confidence"] = None
        m["ai_validated"] = False

    return matches


async def get_validation_stats() -> Dict:
    """Get historical validation stats."""
    if _db is None:
        return {"total_validations": 0, "avg_confidence": 0}

    pipeline = [
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "avg_confidence": {"$avg": "$avg_confidence"},
            "total_symbols": {"$sum": "$symbols_validated"},
        }}
    ]
    cursor = _db.ai_validations.aggregate(pipeline)
    results = await cursor.to_list(length=1)
    if results:
        r = results[0]
        return {
            "total_validations": r.get("total", 0),
            "avg_confidence": round(r.get("avg_confidence", 0), 1),
            "total_symbols_validated": r.get("total_symbols", 0),
        }
    return {"total_validations": 0, "avg_confidence": 0, "total_symbols_validated": 0}
