"""AI routes: chat, hypothesis, research endpoints."""
from fastapi import APIRouter, HTTPException, Request, Form, File, UploadFile
from typing import Optional
import os
import logging
import base64
import re
from datetime import datetime, timezone, timedelta

from services.ai_service import AIService
from services.paper_trading_service import get_portfolio_context
from services.portfolio_agent import run_portfolio_agent
from models.chat import ChatRequest, ChatResponse, ChatSession, ChatMessage
from services.auth_helpers import get_current_user, get_optional_user, is_pro_user
from routes.market_data import _collect_all_scrape_data

router = APIRouter(prefix="/api")
ai_service = AIService()

# Keywords that trigger portfolio context injection
PORTFOLIO_KEYWORDS = re.compile(
    r'\b(portfolio|positions?|holdings?|p&l|pnl|profit|loss|unrealized|'
    r'my stocks?|my trades?|my shares?|how am i doing|trade history|'
    r'paper trad|buy|sell|cash balance|equity|cost basis|'
    r'confirm|proposal|po_|order|place.*order)\b',
    re.IGNORECASE
)

FREE_CHAT_DAILY_LIMIT = 5
VALID_HYPOTHESIS_MODELS = ["gpt-5.2", "claude-sonnet-4.5", "gemini-pro", "consensus"]


# Module-level db reference, set by server.py on startup
db = None

def set_db(database):
    global db
    db = database


# --- Chat Rate Limit ---
@router.get("/chat/limit")
async def get_chat_limit(request: Request):
    """Return remaining chat messages for the user today."""
    user = await get_optional_user(request)
    if not user:
        return {"limit": FREE_CHAT_DAILY_LIMIT, "used": 0, "remaining": FREE_CHAT_DAILY_LIMIT, "is_pro": False}
    if is_pro_user(user):
        return {"limit": -1, "used": 0, "remaining": -1, "is_pro": True}
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    count = await db.chat_usage.count_documents({"user_id": user["_id"], "date": today})
    return {"limit": FREE_CHAT_DAILY_LIMIT, "used": count, "remaining": max(0, FREE_CHAT_DAILY_LIMIT - count), "is_pro": False}


async def _enforce_rate_limit(user: dict):
    """Check and enforce daily chat rate limit for free users. Raises 429 if over limit."""
    if not user or is_pro_user(user):
        return
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    count = await db.chat_usage.count_documents({"user_id": user["_id"], "date": today})
    if count >= FREE_CHAT_DAILY_LIMIT:
        raise HTTPException(status_code=429, detail=f"Free accounts are limited to {FREE_CHAT_DAILY_LIMIT} AI messages per day. Upgrade to Pro for unlimited.")
    await db.chat_usage.insert_one({"user_id": user["_id"], "date": today, "timestamp": datetime.now(timezone.utc).isoformat()})


async def _ensure_session(sessionId: str, user: dict):
    """Create a chat session if it doesn't exist."""
    session = await db.chat_sessions.find_one({"session_id": sessionId}, {"_id": 0, "session_id": 1})
    if not session:
        new_session = ChatSession(session_id=sessionId)
        session_doc = new_session.dict()
        if user:
            session_doc["user_id"] = user["_id"]
        await db.chat_sessions.insert_one(session_doc)


async def _get_memory_context(user: dict) -> str:
    """Load persistent memory context for Pro users."""
    if not user or not is_pro_user(user):
        return ""
    from services.chat_memory_service import get_memory_enabled, get_memory_context
    if await get_memory_enabled(user["_id"]):
        return await get_memory_context(user["_id"])
    return ""


async def _trigger_memory_extraction(user: dict, messages: list, sessionId: str):
    """Trigger async memory extraction for Pro users."""
    if not user or not is_pro_user(user):
        return
    try:
        from services.chat_memory_service import get_memory_enabled, extract_memories_from_conversation
        if await get_memory_enabled(user["_id"]):
            import asyncio
            asyncio.create_task(extract_memories_from_conversation(user["_id"], messages, sessionId))
    except Exception as e:
        logging.warning(f"Memory extraction trigger failed: {e}")


# --- AI Chat ---
@router.post("/chat")
async def chat(
    request: Request,
    message: str = Form(...),
    sessionId: str = Form(...),
    image: Optional[UploadFile] = File(None),
):
    try:
        user = await get_optional_user(request)
        await _enforce_rate_limit(user)

        # Deduct credits (Pro/Pro Max get chat FREE)
        if user:
            from services.credit_service import deduct_credits, get_user_plan
            plan_key = get_user_plan(user)
            credit_result = await deduct_credits(str(user["_id"]), "chat", plan_key)
            if not credit_result["allowed"]:
                return JSONResponse(status_code=402, content={
                    "error": "insufficient_credits",
                    "detail": credit_result.get("error", "Not enough credits"),
                    "cost": credit_result["cost"],
                    "remaining": credit_result["remaining"],
                    "upgrade_options": credit_result.get("upgrade_options", []),
                })

        image_base64 = None
        if image and image.filename:
            content = await image.read()
            image_base64 = base64.b64encode(content).decode()

        await _ensure_session(sessionId, user)
        memory_context = await _get_memory_context(user)

        is_portfolio_query = user and PORTFOLIO_KEYWORDS.search(message) and not image_base64
        if is_portfolio_query:
            try:
                ai_response = await run_portfolio_agent(user["_id"], message)
            except Exception as e:
                logging.warning(f"Portfolio agent failed, falling back to standard chat: {e}")
                ai_response = await ai_service.chat(message, sessionId, image_base64, memory_context=memory_context)
        else:
            ai_response = await ai_service.chat(message, sessionId, image_base64, memory_context=memory_context)

        user_message = ChatMessage(role="user", content=message, image_base64="[image_attached]" if image_base64 else None)
        assistant_message = ChatMessage(role="assistant", content=ai_response)

        await db.chat_sessions.update_one(
            {"session_id": sessionId},
            {"$push": {"messages": {"$each": [user_message.dict(), assistant_message.dict()]}},
             "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}}
        )

        await _trigger_memory_extraction(user, [user_message.dict(), assistant_message.dict()], sessionId)
        return {"response": ai_response, "sessionId": sessionId}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail="Error processing chat request")


@router.get("/chat/history/{session_id}")
async def get_chat_history(session_id: str, request: Request):
    try:
        session = await db.chat_sessions.find_one({"session_id": session_id}, {"_id": 0, "messages": 1})
        if not session:
            return {"messages": []}
        messages = session.get("messages", [])

        # Free users: only get messages from last 24 hours
        user = await get_optional_user(request)
        if user and not is_pro_user(user):
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            messages = [m for m in messages if m.get("timestamp", "9999") >= cutoff]

        return {"messages": messages, "history_limited": bool(user) and not is_pro_user(user)}
    except Exception as e:
        logging.error(f"Error fetching chat history: {e}")
        raise HTTPException(status_code=500, detail="Error fetching chat history")


# --- PDF Export (Pro Only) ---
@router.get("/export/hypothesis/{symbol}")
async def export_hypothesis_pdf(symbol: str, request: Request):
    """Generate a PDF report for a hypothesis. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="PDF export is a Pro feature. Upgrade to unlock.")
    # Return JSON data that frontend will format into PDF
    from services.hypothesis_service import HypothesisService
    from services.financial_scraping_service import FinancialScrapingService
    from services.world_events_service import WorldEventsService
    from services.foreign_markets_service import ForeignMarketsService

    financial_scraper = FinancialScrapingService()
    news = await financial_scraper.scrape_financial_news()
    social = await financial_scraper.scrape_reddit_sentiment()
    world_events = await WorldEventsService().scrape_world_events()
    foreign_markets = await ForeignMarketsService().get_foreign_markets()

    data = {"news": news, "social": social, "world_events": world_events, "foreign_markets": foreign_markets}
    hypothesis_svc = HypothesisService(os.environ.get("EMERGENT_LLM_KEY"))
    hypothesis = await hypothesis_svc.generate_hypothesis(symbol, data)
    hypothesis["export"] = True
    hypothesis["exported_at"] = datetime.now(timezone.utc).isoformat()
    hypothesis["exported_by"] = user.get("name", user.get("email"))
    return hypothesis


# --- Market Signals (Pro Only) ---
@router.get("/signals")
async def get_market_signals(request: Request):
    """Get AI-detected market signals for the user's watchlist."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        return {"signals": [], "is_pro": False}
    cursor = db.market_signals.find(
        {"user_id": user["_id"]}, {"_id": 0, "symbol": 1, "signal_type": 1, "direction": 1, "confidence": 1, "detected_at": 1, "message": 1}
    ).sort("detected_at", -1).limit(20)
    signals = []
    async for doc in cursor:
        signals.append(doc)
    return {"signals": signals, "is_pro": True}


@router.post("/signals/scan")
async def scan_for_signals(request: Request):
    """Manually trigger a signal scan for the user's watchlist tickers."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Market signals is a Pro feature.")

    wl = await db.watchlists.find_one({"user_id": user["_id"]})
    tickers = wl.get("tickers", []) if wl else []
    if not tickers:
        return {"signals": [], "message": "No tickers in watchlist"}

    from services.market_data_service import MarketDataService
    market_svc = MarketDataService()

    signals_found = []
    for ticker in tickers[:10]:
        try:
            signals_found.extend(await _scan_dark_pool(market_svc, user["_id"], ticker))
            signals_found.extend(await _scan_options_flow(market_svc, user["_id"], ticker))
        except Exception as e:
            logging.warning(f"Signal scan error for {ticker}: {e}")

    return {"signals": signals_found, "tickers_scanned": len(tickers[:10])}


async def _scan_dark_pool(market_svc, user_id: str, ticker: str) -> list:
    """Check for dark pool volume spikes on a ticker using Polygon data."""
    try:
        from services.polygon_dark_pool_service import fetch_dark_pool_data
        dp_data = await fetch_dark_pool_data()
        rows = dp_data.get("dark_pool", [])
    except Exception:
        rows = []
    dp_match = [d for d in rows if d.get("ticker") == ticker]
    if dp_match and dp_match[0].get("dark_pool_volume", 0) > 500000:
        vol = dp_match[0]["dark_pool_volume"]
        pct = dp_match[0].get("dark_pool_pct", 0)
        sig = {
            "user_id": user_id, "ticker": ticker, "type": "dark_pool_spike",
            "title": f"Dark Pool Spike: {ticker}",
            "detail": f"Dark pool volume: {vol:,} shares ({pct}% of total)",
            "severity": "high", "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.market_signals.insert_one(sig)
        del sig["user_id"]
        return [sig]
    return []


async def _scan_options_flow(market_svc, user_id: str, ticker: str) -> list:
    """Check for notable options flow activity on a ticker."""
    options = market_svc.generate_mock_options_data('flow')
    opt_match = [o for o in options if o.get("symbol") == ticker or o.get("contract", "").startswith(ticker)]
    results = []
    for o in opt_match[:1]:
        sig = {
            "user_id": user_id, "ticker": ticker, "type": "options_flow",
            "title": f"Options Activity: {ticker}",
            "detail": f"Notable options flow detected — {o.get('side', 'N/A')} {o.get('contract', ticker)}",
            "severity": "medium", "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.market_signals.insert_one(sig)
        del sig["user_id"]
        results.append(sig)
    return results


# --- Portfolio Analyzer (Pro Only) ---
@router.post("/portfolio/analyze")
async def analyze_portfolio(request: Request):
    """AI Portfolio Analyzer — input holdings, get health score + suggestions."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Portfolio Analyzer is a Pro feature. Upgrade to unlock.")

    body = await request.json()
    holdings = body.get("holdings", [])
    if not holdings:
        raise HTTPException(status_code=400, detail="Please provide at least one holding.")

    prompt, total_value = await _build_portfolio_prompt(holdings)

    try:
        result = await _run_portfolio_analysis(prompt, user["_id"])
        result["total_value"] = total_value
        result["holdings_count"] = len(holdings)
        result["analyzed_at"] = datetime.now(timezone.utc).isoformat()
        return result
    except Exception as e:
        logging.error(f"Portfolio analysis error: {e}")
        raise HTTPException(status_code=500, detail="Error analyzing portfolio")


async def _build_portfolio_prompt(holdings: list) -> tuple:
    """Build the AI prompt with holdings and macro context. Returns (prompt, total_value)."""
    from services.world_events_service import WorldEventsService
    from services.foreign_markets_service import ForeignMarketsService
    world_events = await WorldEventsService().scrape_world_events()
    foreign_markets = await ForeignMarketsService().get_foreign_markets()

    holdings_text = "\n".join([f"- {h.get('ticker', 'UNKNOWN')}: {h.get('shares', 0)} shares @ ${h.get('avg_price', 0)}" for h in holdings])
    total_value = sum(h.get("shares", 0) * h.get("avg_price", 0) for h in holdings)

    prompt = f"""You are an expert portfolio analyst for RISEDUAL AI. Analyze this portfolio and provide a health score and rebalancing suggestions.

PORTFOLIO (Total Value: ${total_value:,.2f}):
{holdings_text}

CURRENT MACRO CONTEXT:
- World Events: {world_events.get('total_events', 0)} tracked, {world_events.get('high_impact_count', 0)} high-impact
- Top Affected Sectors: {', '.join([s['sector'] for s in world_events.get('affected_sectors', [])[:5]])}
- Foreign Market Signals: {len(foreign_markets.get('correlation_signals', []))} correlation signals

Respond in this EXACT JSON format:
{{
  "health_score": <0-100>,
  "risk_level": "<low|medium|high|critical>",
  "diversification_grade": "<A|B|C|D|F>",
  "sector_exposure": "<brief description>",
  "top_risk": "<biggest risk in portfolio>",
  "suggestions": ["<suggestion 1>", "<suggestion 2>", "<suggestion 3>"],
  "rebalance_actions": [
    {{"ticker": "<TICKER>", "action": "<buy|sell|hold>", "reason": "<why>"}}
  ],
  "summary": "<2-3 sentence overall assessment>"
}}"""
    return prompt, total_value


async def _run_portfolio_analysis(prompt: str, user_id: str) -> dict:
    """Run the LLM analysis and parse the JSON response."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    import json
    llm_key = os.environ.get("EMERGENT_LLM_KEY")
    llm = LlmChat(
        api_key=llm_key,
        session_id=f"portfolio_{user_id}",
        system_message="You are a professional portfolio analyst. Return ONLY valid JSON."
    ).with_model("openai", "gpt-5.2")
    response = await llm.send_message(UserMessage(text=prompt))
    text = response.strip() if isinstance(response, str) else response
    try:
        return json.loads(text.strip().strip("```json").strip("```"))
    except json.JSONDecodeError:
        return {"health_score": 50, "summary": text, "suggestions": [], "rebalance_actions": [], "risk_level": "medium", "diversification_grade": "C"}


# --- Company Research ---
@router.get("/research/{symbol}")
async def research_company(symbol: str):
    try:
        from services.company_research_service import CompanyResearchService
        service = CompanyResearchService()
        session_id = f"research_{symbol}_{datetime.now(timezone.utc).isoformat()}"
        return await service.research_company(symbol, session_id)
    except Exception as e:
        logging.error(f"Error researching {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- AI Hypothesis ---
@router.get("/hypothesis/{symbol}")
async def get_hypothesis(symbol: str, request: Request, model: str = "gpt-5.2"):
    user = await get_optional_user(request)
    is_pro = is_pro_user(user)

    if model not in VALID_HYPOTHESIS_MODELS:
        model = "gpt-5.2"
    if not is_pro and model != "gpt-5.2":
        raise HTTPException(status_code=403, detail="Premium AI models require a Pro subscription. Free users can use GPT-5.2.")

    try:
        data = await _collect_all_scrape_data()

        if not is_pro:
            return _build_hypothesis_teaser(symbol, data)

        from services.multi_model_hypothesis_service import generate_hypothesis
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        hypothesis = await generate_hypothesis(api_key, symbol, data, model=model)
        hypothesis["is_pro"] = True

        # Log prediction for accuracy tracking
        if hypothesis.get("verdict") and db is not None:
            try:
                from services.prediction_tracker import log_prediction
                await log_prediction(
                    db, "hypothesis", symbol.upper(),
                    hypothesis["verdict"],
                    hypothesis.get("confidence", 0),
                    user_id=str(user.get("_id", ""))
                )
            except Exception as track_err:
                logging.warning(f"Prediction tracking failed: {track_err}")

        await _track_verdict_change(user, symbol, hypothesis)
        return hypothesis
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error generating hypothesis for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Error generating hypothesis")


def _build_hypothesis_teaser(symbol: str, data: dict) -> dict:
    """Build the locked teaser response for free users."""
    news_count = len(data.get("news", []))
    social_count = len(data.get("social", []))
    crypto_count = len(data.get("crypto_data", []))
    events_count = data.get("world_events", {}).get("total_events", 0)
    congress_count = data.get("gov_filings", {}).get("congressional_count", 0)

    return {
        "symbol": symbol.upper(),
        "is_pro": False,
        "teaser": {
            "data_sources_count": news_count + social_count + crypto_count,
            "world_events_count": events_count,
            "congressional_trades_count": congress_count,
            "verdict": "LOCKED",
            "summary": (
                f"Our AI has analyzed {news_count} news articles, {events_count} world events, "
                f"and {congress_count} congressional trades to generate a hypothesis for "
                f"{symbol.upper()}. Subscribe to Pro to unlock the full analysis."
            ),
        },
    }


async def _track_verdict_change(user, symbol: str, hypothesis: dict):
    """Check if verdict changed and create a notification if so."""
    if not user or not hypothesis.get("verdict"):
        return
    try:
        prev = await db.hypothesis_history.find_one(
            {"user_id": user["_id"], "symbol": symbol.upper()},
            {"_id": 0, "verdict": 1},
            sort=[("searched_at", -1)]
        )
        if not prev or not prev.get("verdict") or prev["verdict"] == hypothesis["verdict"]:
            return

        wl = await db.watchlists.find_one({"user_id": user["_id"]}, {"_id": 0, "tickers": 1})
        tickers = wl.get("tickers", []) if wl else []
        await db.notifications.insert_one({
            "user_id": user["_id"],
            "type": "verdict_change",
            "symbol": symbol.upper(),
            "old_verdict": prev["verdict"],
            "new_verdict": hypothesis["verdict"],
            "confidence": hypothesis.get("confidence", 0),
            "in_watchlist": symbol.upper() in tickers,
            "read": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as notif_err:
        logging.warning(f"Notification creation error: {notif_err}")


# ─── Voice: Text-to-Speech ───
@router.post("/chat/tts")
async def text_to_speech(request: Request):
    """Convert text to speech using OpenAI TTS via Emergent."""
    try:
        body = await request.json()
        text = body.get("text", "")
        voice = body.get("voice", "nova")  # nova=female, onyx=male
        if not text:
            raise HTTPException(status_code=400, detail="No text provided")

        # Truncate to TTS limit (4096 chars)
        text = text[:4096]

        from emergentintegrations.llm.openai import OpenAITextToSpeech
        tts = OpenAITextToSpeech(api_key=os.getenv("EMERGENT_LLM_KEY"))
        audio_base64 = await tts.generate_speech_base64(
            text=text,
            model="tts-1",
            voice=voice,
            response_format="mp3",
            speed=1.0,
        )
        return {"audio": audio_base64, "format": "mp3", "voice": voice}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {str(e)}")


# ─── Voice: Speech-to-Text ───
@router.post("/chat/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    """Transcribe audio to text using OpenAI Whisper via Emergent."""
    try:
        audio_bytes = await audio.read()
        if len(audio_bytes) > 25 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Audio file too large (max 25MB)")

        import tempfile
        suffix = ".webm"
        if audio.filename:
            if audio.filename.endswith(".wav"):
                suffix = ".wav"
            elif audio.filename.endswith(".mp3"):
                suffix = ".mp3"
            elif audio.filename.endswith(".m4a"):
                suffix = ".m4a"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        from emergentintegrations.llm.openai import OpenAISpeechToText
        stt = OpenAISpeechToText(api_key=os.getenv("EMERGENT_LLM_KEY"))
        with open(tmp_path, "rb") as f:
            response = await stt.transcribe(
                file=f,
                model="whisper-1",
                response_format="json",
                language="en",
            )

        os.unlink(tmp_path)
        return {"text": response.text}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"STT error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")


# ── Chat Memory Endpoints (Pro only) ──

@router.get("/chat/memory")
async def get_chat_memories(request: Request):
    """Get all stored memories for the current user. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    from services.chat_memory_service import get_memories, get_memory_enabled
    memories = await get_memories(user["_id"])
    enabled = await get_memory_enabled(user["_id"])
    return {"memories": memories, "enabled": enabled, "count": len(memories)}


@router.post("/chat/memory/toggle")
async def toggle_chat_memory(request: Request):
    """Toggle chat memory on/off."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    body = await request.json()
    enabled = body.get("enabled", True)
    from services.chat_memory_service import set_memory_enabled
    await set_memory_enabled(user["_id"], enabled)
    return {"enabled": enabled}


@router.delete("/chat/memory/{memory_id}")
async def delete_chat_memory(memory_id: str, request: Request):
    """Delete a specific memory."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    from services.chat_memory_service import delete_memory
    deleted = await delete_memory(user["_id"], memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True, "memory_id": memory_id}


@router.delete("/chat/memory")
async def clear_chat_memories(request: Request):
    """Clear all memories for the current user."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    from services.chat_memory_service import clear_all_memories
    count = await clear_all_memories(user["_id"])
    return {"cleared": count}


MAX_PINNED_MEMORIES = 5

@router.post("/chat/memory/pin")
async def pin_chat_memory(request: Request):
    """Manually pin a message to memory. Max 5 pinned memories. Pro only.
    
    Body: {"content": "The text to remember"}
    """
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required")
    
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")
    if len(content) > 500:
        content = content[:500]
    
    from services.chat_memory_service import get_memories, save_memory
    
    # Count existing pinned memories
    existing = await get_memories(user["_id"], limit=100)
    pinned_count = sum(1 for m in existing if m.get("category") == "pinned")
    
    if pinned_count >= MAX_PINNED_MEMORIES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_PINNED_MEMORIES} pinned memories allowed. Delete one to pin a new one.")
    
    memory_id = await save_memory(user["_id"], content, "pinned", "manual")
    return {"pinned": True, "memory_id": memory_id, "pinned_count": pinned_count + 1, "max": MAX_PINNED_MEMORIES}
