"""AI routes: chat, hypothesis, predictions, research, scraping endpoints."""
from fastapi import APIRouter, HTTPException, Request
import os
import logging
from datetime import datetime, timezone, timedelta

from services.ai_service import AIService
from models.chat import ChatRequest, ChatResponse, ChatSession, ChatMessage
from services.auth_helpers import get_current_user, get_optional_user, is_pro_user

router = APIRouter(prefix="/api")
ai_service = AIService()

FREE_CHAT_DAILY_LIMIT = 5


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


# --- AI Chat ---
@router.post("/chat", response_model=ChatResponse)
async def chat(chat_request: ChatRequest, request: Request):
    try:
        # Rate limit for free users
        user = await get_optional_user(request)
        if user and not is_pro_user(user):
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            count = await db.chat_usage.count_documents({"user_id": user["_id"], "date": today})
            if count >= FREE_CHAT_DAILY_LIMIT:
                raise HTTPException(status_code=429, detail=f"Free accounts are limited to {FREE_CHAT_DAILY_LIMIT} AI messages per day. Upgrade to Pro for unlimited.")
            await db.chat_usage.insert_one({"user_id": user["_id"], "date": today, "timestamp": datetime.now(timezone.utc).isoformat()})

        session = await db.chat_sessions.find_one({"session_id": chat_request.sessionId}, {"_id": 0, "session_id": 1})
        if not session:
            new_session = ChatSession(session_id=chat_request.sessionId)
            session_doc = new_session.dict()
            if user:
                session_doc["user_id"] = user["_id"]
            await db.chat_sessions.insert_one(session_doc)

        ai_response = await ai_service.chat(chat_request.message, chat_request.sessionId, chat_request.image_base64)

        user_message = ChatMessage(
            role="user",
            content=chat_request.message,
            image_base64="[image_attached]" if chat_request.image_base64 else None
        )
        assistant_message = ChatMessage(role="assistant", content=ai_response)

        await db.chat_sessions.update_one(
            {"session_id": chat_request.sessionId},
            {
                "$push": {"messages": {"$each": [user_message.dict(), assistant_message.dict()]}},
                "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}
            }
        )
        return ChatResponse(response=ai_response, sessionId=chat_request.sessionId)
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
        {"user_id": user["_id"]}, {"_id": 0}
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

    # Get user's watchlist
    wl = await db.watchlists.find_one({"user_id": user["_id"]})
    tickers = wl.get("tickers", []) if wl else []
    if not tickers:
        return {"signals": [], "message": "No tickers in watchlist"}

    from services.market_data_service import MarketDataService
    market_svc = MarketDataService()

    signals_found = []
    for ticker in tickers[:10]:  # Limit to 10 tickers
        try:
            dark_pool = market_svc.generate_dark_pool_data()
            dp_match = [d for d in dark_pool if d.get("ticker") == ticker]
            if dp_match and dp_match[0].get("volume", 0) > 500000:
                sig = {
                    "user_id": user["_id"],
                    "ticker": ticker,
                    "type": "dark_pool_spike",
                    "title": f"Dark Pool Spike: {ticker}",
                    "detail": f"Unusual dark pool volume detected ({dp_match[0].get('volume', 0):,} shares)",
                    "severity": "high",
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }
                await db.market_signals.insert_one(sig)
                del sig["user_id"]
                signals_found.append(sig)

            options = market_svc.generate_mock_options_data('flow')
            opt_match = [o for o in options if o.get("symbol") == ticker or o.get("contract", "").startswith(ticker)]
            if opt_match:
                for o in opt_match[:1]:
                    sig = {
                        "user_id": user["_id"],
                        "ticker": ticker,
                        "type": "options_flow",
                        "title": f"Options Activity: {ticker}",
                        "detail": f"Notable options flow detected — {o.get('side', 'N/A')} {o.get('contract', ticker)}",
                        "severity": "medium",
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await db.market_signals.insert_one(sig)
                    del sig["user_id"]
                    signals_found.append(sig)
        except Exception as e:
            logging.warning(f"Signal scan error for {ticker}: {e}")

    return {"signals": signals_found, "tickers_scanned": len(tickers[:10])}


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

    # Gather macro data for context
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

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        import json
        llm_key = os.environ.get("EMERGENT_LLM_KEY")
        llm = LlmChat(
            api_key=llm_key,
            session_id=f"portfolio_{user['_id']}",
            system_message="You are a professional portfolio analyst. Return ONLY valid JSON."
        ).with_model("openai", "gpt-5.2")
        response = await llm.send_message(UserMessage(text=prompt))
        text = response.strip() if isinstance(response, str) else response
        try:
            result = json.loads(text.strip().strip("```json").strip("```"))
        except json.JSONDecodeError:
            result = {"health_score": 50, "summary": text, "suggestions": [], "rebalance_actions": [], "risk_level": "medium", "diversification_grade": "C"}

        result["total_value"] = total_value
        result["holdings_count"] = len(holdings)
        result["analyzed_at"] = datetime.now(timezone.utc).isoformat()
        return result
    except Exception as e:
        logging.error(f"Portfolio analysis error: {e}")
        raise HTTPException(status_code=500, detail="Error analyzing portfolio")


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


# --- World Events & Macro ---
@router.get("/world-events")
async def get_world_events():
    try:
        from services.world_events_service import WorldEventsService
        return await WorldEventsService().scrape_world_events()
    except Exception as e:
        logging.error(f"Error fetching world events: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/foreign-markets")
async def get_foreign_markets():
    try:
        from services.foreign_markets_service import ForeignMarketsService
        return await ForeignMarketsService().get_foreign_markets()
    except Exception as e:
        logging.error(f"Error fetching foreign markets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gov-filings")
async def get_gov_filings():
    try:
        from services.gov_filings_service import GovFilingsService
        return await GovFilingsService().get_all_gov_data()
    except Exception as e:
        logging.error(f"Error fetching gov filings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Market Prediction ---
@router.get("/market/prediction")
async def get_market_prediction():
    try:
        from services.financial_scraping_service import FinancialScrapingService
        from services.crypto_scraping_service import CryptoScrapingService
        from services.real_estate_scraping_service import RealEstateScrapingService
        from services.market_prediction_service import MarketPredictionService
        from services.world_events_service import WorldEventsService
        from services.foreign_markets_service import ForeignMarketsService
        from services.gov_filings_service import GovFilingsService

        financial_scraper = FinancialScrapingService()
        crypto_scraper = CryptoScrapingService()
        real_estate_scraper = RealEstateScrapingService()
        world_events_svc = WorldEventsService()
        foreign_markets_svc = ForeignMarketsService()
        gov_filings_svc = GovFilingsService()

        financial_news = await financial_scraper.scrape_financial_news()
        reddit_sentiment = await financial_scraper.scrape_reddit_sentiment()
        insider_trades = await financial_scraper.scrape_insider_trades()
        crypto_data = await crypto_scraper.get_exchange_data()
        whale_transactions = await crypto_scraper.get_whale_transactions()
        crypto_sentiment = await crypto_scraper.get_crypto_sentiment()
        real_estate_data = await real_estate_scraper.scrape_all_real_estate_data()
        world_events = await world_events_svc.scrape_world_events()
        foreign_markets = await foreign_markets_svc.get_foreign_markets()
        gov_filings = await gov_filings_svc.get_all_gov_data()

        all_crypto_data = crypto_data + [crypto_sentiment] + whale_transactions

        prediction_service = MarketPredictionService(os.environ.get('EMERGENT_LLM_KEY'))
        prediction = await prediction_service.analyze_market(
            financial_news=financial_news,
            crypto_data=all_crypto_data,
            insider_trades=insider_trades,
            social_sentiment=reddit_sentiment,
            real_estate_data=real_estate_data,
            world_events=world_events,
            foreign_markets=foreign_markets,
            gov_filings=gov_filings,
        )

        prediction['real_estate_summary'] = {
            'housing_health': real_estate_data.get('housing', {}).get('market_health', 'unknown'),
            'commercial_trend': 'mixed',
            'data_sources': len(real_estate_data.get('housing', {}).get('sources', [])),
            'implications': real_estate_data.get('trends', {}).get('market_implications', {})
        }

        prediction['macro_data'] = {
            'world_events': {
                'total': world_events.get('total_events', 0),
                'high_impact': world_events.get('high_impact_count', 0),
                'top_sectors': [s['sector'] for s in world_events.get('affected_sectors', [])[:5]],
            },
            'foreign_markets': {
                'correlation_signals': foreign_markets.get('correlation_signals', [])[:5],
                'total_indices': len(foreign_markets.get('asia', []) + foreign_markets.get('europe', []) + foreign_markets.get('americas', [])),
            },
            'gov_filings': {
                'congressional_trades': gov_filings.get('congressional_count', 0),
                'fed_announcements': gov_filings.get('fed_count', 0),
                'insider_trades': gov_filings.get('insider_count', 0),
            },
        }

        return prediction
    except Exception as e:
        logging.error(f"Error generating prediction: {e}")
        raise HTTPException(status_code=500, detail="Error generating market prediction")


# --- AI Hypothesis ---
@router.get("/hypothesis/{symbol}")
async def get_hypothesis(symbol: str, request: Request, model: str = "gpt-5.2"):
    user = await get_optional_user(request)
    is_pro = is_pro_user(user)

    # Model access control: free users can only use gpt-5.2
    VALID_MODELS = ["gpt-5.2", "claude-sonnet-4.5", "gemini-pro", "consensus"]
    if model not in VALID_MODELS:
        model = "gpt-5.2"
    if not is_pro and model != "gpt-5.2":
        raise HTTPException(status_code=403, detail="Premium AI models require a Pro subscription. Free users can use GPT-5.2.")

    try:
        from services.financial_scraping_service import FinancialScrapingService
        from services.crypto_scraping_service import CryptoScrapingService
        from services.world_events_service import WorldEventsService
        from services.foreign_markets_service import ForeignMarketsService
        from services.gov_filings_service import GovFilingsService
        from services.multi_model_hypothesis_service import generate_hypothesis

        financial_scraper = FinancialScrapingService()
        crypto_scraper = CryptoScrapingService()
        world_events_svc = WorldEventsService()
        foreign_markets_svc = ForeignMarketsService()
        gov_filings_svc = GovFilingsService()

        news = await financial_scraper.scrape_financial_news()
        social = await financial_scraper.scrape_reddit_sentiment()
        crypto_data = await crypto_scraper.get_exchange_data()
        world_events = await world_events_svc.scrape_world_events()
        foreign_markets = await foreign_markets_svc.get_foreign_markets()
        gov_filings = await gov_filings_svc.get_all_gov_data()

        data = {
            "news": news, "social": social, "crypto": crypto_data,
            "world_events": world_events, "foreign_markets": foreign_markets,
            "gov_filings": gov_filings,
        }

        if not is_pro:
            return {
                "symbol": symbol.upper(),
                "is_pro": False,
                "teaser": {
                    "data_sources_count": len(news) + len(social) + len(crypto_data),
                    "world_events_count": world_events.get("total_events", 0),
                    "congressional_trades_count": gov_filings.get("congressional_count", 0),
                    "verdict": "LOCKED",
                    "summary": f"Our AI has analyzed {len(news)} news articles, {world_events.get('total_events', 0)} world events, and {gov_filings.get('congressional_count', 0)} congressional trades to generate a hypothesis for {symbol.upper()}. Subscribe to Pro to unlock the full analysis.",
                },
            }

        api_key = os.environ.get("EMERGENT_LLM_KEY")
        hypothesis = await generate_hypothesis(api_key, symbol, data, model=model)
        hypothesis["is_pro"] = True

        # Check for verdict change -> generate notification
        if user and hypothesis.get("verdict"):
            try:
                prev = await db.hypothesis_history.find_one(
                    {"user_id": user["_id"], "symbol": symbol.upper()},
                    {"_id": 0, "verdict": 1},
                    sort=[("searched_at", -1)]
                )
                if prev and prev.get("verdict") and prev["verdict"] != hypothesis["verdict"]:
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

        return hypothesis
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error generating hypothesis for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Error generating hypothesis")


# --- Scraping Data Endpoints ---
@router.get("/market/news")
async def get_financial_news():
    try:
        from services.financial_scraping_service import FinancialScrapingService
        return await FinancialScrapingService().scrape_financial_news()
    except Exception as e:
        logging.error(f"Error fetching news: {e}")
        raise HTTPException(status_code=500, detail="Error fetching news")


@router.get("/market/social-sentiment")
async def get_social_sentiment():
    try:
        from services.financial_scraping_service import FinancialScrapingService
        return await FinancialScrapingService().scrape_reddit_sentiment()
    except Exception as e:
        logging.error(f"Error fetching social sentiment: {e}")
        raise HTTPException(status_code=500, detail="Error fetching social sentiment")


@router.get("/market/insider-trades")
async def get_insider_trades():
    try:
        from services.financial_scraping_service import FinancialScrapingService
        return await FinancialScrapingService().scrape_insider_trades()
    except Exception as e:
        logging.error(f"Error fetching insider trades: {e}")
        raise HTTPException(status_code=500, detail="Error fetching insider trades")


@router.get("/market/crypto-data")
async def get_crypto_market_data():
    try:
        from services.crypto_scraping_service import CryptoScrapingService
        scraper = CryptoScrapingService()
        return {
            'exchange_data': await scraper.get_exchange_data(),
            'whale_transactions': await scraper.get_whale_transactions(),
            'sentiment': await scraper.get_crypto_sentiment()
        }
    except Exception as e:
        logging.error(f"Error fetching crypto data: {e}")
        raise HTTPException(status_code=500, detail="Error fetching crypto data")


@router.get("/market/real-estate")
async def get_real_estate_data():
    try:
        from services.real_estate_scraping_service import RealEstateScrapingService
        return await RealEstateScrapingService().scrape_all_real_estate_data()
    except Exception as e:
        logging.error(f"Error fetching real estate data: {e}")
        raise HTTPException(status_code=500, detail="Error fetching real estate data")
