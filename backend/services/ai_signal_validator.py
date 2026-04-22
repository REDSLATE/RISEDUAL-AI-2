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
from typing import Optional

logger = logging.getLogger(__name__)

_db = None


def set_db(database: object) -> None:
    global _db
    _db = database


def _build_validation_prompt(matches: list[dict], strategy_name: str) -> str:
    """Build the AI prompt for signal validation."""
    symbols_data = []
    for m in matches:
        symbols_data.append(
            f"- {m['symbol']}: ${m.get('price', '?')} | RSI={m.get('rsi', '?')} | "
            f"Vol={m.get('vol_ratio', m.get('volume_ratio', '?'))}x | Trend={m.get('trend', '?')} | "
            f"Signal: {m.get('detail', strategy_name)}"
        )

    return f"""You are an adversarial AI signal validator for a financial research publishing platform. Your job is to critically evaluate scanner signals and filter out weak ones. Present findings as observations, not recommendations.

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


def _parse_ai_response(response_text: str) -> list[dict]:
    """Extract and parse JSON array from AI response."""
    text = str(response_text).strip()
    bracket_start = text.find("[")
    bracket_end = text.rfind("]")
    if bracket_start >= 0 and bracket_end > bracket_start:
        text = text[bracket_start:bracket_end + 1]
    return json.loads(text)


def _merge_ai_results(batch: list[dict], ai_results: list[dict]) -> None:
    """Merge AI validation results back into the original match dicts.

    Also fires off a best-effort rejection log for any symbol the auditor
    decided to hold or reject — captured as hard negatives for retraining.
    """
    from services.rejection_log import log_rejected as _log_rejected  # lazy
    import asyncio as _asyncio

    ai_map = {r["symbol"]: r for r in ai_results if isinstance(r, dict) and "symbol" in r}
    for m in batch:
        ai = ai_map.get(m["symbol"], {})
        m["ai_confidence"] = ai.get("ai_confidence", 50)
        m["ai_verdict"] = ai.get("verdict", "hold")
        m["ai_risk"] = ai.get("risk_level", "medium")
        m["ai_reasoning"] = ai.get("reasoning", "")
        m["ai_action"] = ai.get("recommended_action", "")
        m["ai_validated"] = True

        # Log rejections (verdict != "buy") so retraining can see what the
        # Auditor overruled and the eventual outcome becomes a training
        # signal. Fire-and-forget — must never break the merge path.
        verdict = str(m.get("ai_verdict") or "").lower()
        if verdict and verdict != "buy":
            try:
                _asyncio.create_task(_log_rejected(
                    asset=m.get("symbol", ""),
                    direction=None,
                    reason=f"ai_verdict={verdict} (conf={m.get('ai_confidence')})",
                    source="ai_signal_validator",
                    meta={
                        "verdict": verdict,
                        "risk_level": m.get("ai_risk"),
                        "ai_confidence": m.get("ai_confidence"),
                        "reasoning": (m.get("ai_reasoning") or "")[:500],
                    },
                ))
            except Exception:
                pass


def _mark_unvalidated(matches: list[dict], error_msg: Optional[str] = None) -> None:
    """Mark matches as not validated (used on error or for overflow items)."""
    for m in matches:
        m["ai_confidence"] = None
        m["ai_validated"] = False
        if error_msg:
            m["ai_reasoning"] = error_msg


async def _log_validation(strategy_name: str, batch: list[dict]) -> None:
    """Log validation run to MongoDB for analytics."""
    if _db is None:
        return
    await _db.ai_validations.insert_one({
        "strategy": strategy_name,
        "symbols_validated": len(batch),
        "avg_confidence": round(sum(m.get("ai_confidence", 50) for m in batch) / len(batch), 1),
        "validated_at": datetime.now(timezone.utc).isoformat(),
    })


async def validate_signals(matches: list[dict], strategy_name: str = "") -> list[dict]:
    """Validate a batch of scanner matches using AI. Returns enriched matches with AI scores."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key or not matches:
        return matches

    batch = matches[:10]
    prompt = _build_validation_prompt(batch, strategy_name)

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        session_id = f"validate_{datetime.now(timezone.utc).strftime('%H%M%S')}"
        chat = LlmChat(
            api_key=api_key, session_id=session_id,
            system_message="You are an expert quantitative analyst for a financial research publishing platform. Return only valid JSON arrays. Present findings as observations."
        ).with_model("openai", "gpt-5.2")

        response = await chat.send_message(UserMessage(text=prompt))
        ai_results = _parse_ai_response(response)
        _merge_ai_results(batch, ai_results)
        await _log_validation(strategy_name, batch)

    except Exception as e:
        logger.error(f"AI validation failed: {e}")
        _mark_unvalidated(batch, "Validation unavailable")

    _mark_unvalidated(matches[10:])
    return matches


async def get_validation_stats() -> dict:
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
