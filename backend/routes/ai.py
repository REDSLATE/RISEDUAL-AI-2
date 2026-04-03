"""AI routes: chat, hypothesis, predictions, research, scraping endpoints."""
from fastapi import APIRouter, HTTPException, Request
import os
import logging
from datetime import datetime, timezone

from services.ai_service import AIService
from models.chat import ChatRequest, ChatResponse, ChatSession, ChatMessage
from routes.auth import get_current_user, get_optional_user

router = APIRouter(prefix="/api")
ai_service = AIService()

# Module-level db reference, set by server.py on startup
db = None

def set_db(database):
    global db
    db = database


# --- AI Chat ---
@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        session = await db.chat_sessions.find_one({"session_id": request.sessionId})
        if not session:
            new_session = ChatSession(session_id=request.sessionId)
            await db.chat_sessions.insert_one(new_session.dict())

        ai_response = await ai_service.chat(request.message, request.sessionId, request.image_base64)

        user_message = ChatMessage(
            role="user",
            content=request.message,
            image_base64="[image_attached]" if request.image_base64 else None
        )
        assistant_message = ChatMessage(role="assistant", content=ai_response)

        await db.chat_sessions.update_one(
            {"session_id": request.sessionId},
            {
                "$push": {"messages": {"$each": [user_message.dict(), assistant_message.dict()]}},
                "$set": {"updated_at": datetime.now(timezone.utc)}
            }
        )
        return ChatResponse(response=ai_response, sessionId=request.sessionId)
    except Exception as e:
        logging.error(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail="Error processing chat request")


@router.get("/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    try:
        session = await db.chat_sessions.find_one({"session_id": session_id})
        if not session:
            return {"messages": []}
        return {"messages": session.get("messages", [])}
    except Exception as e:
        logging.error(f"Error fetching chat history: {e}")
        raise HTTPException(status_code=500, detail="Error fetching chat history")


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
async def get_hypothesis(symbol: str, request: Request):
    user = await get_optional_user(request)
    is_pro = user and user.get("subscription_status") == "pro"

    try:
        from services.financial_scraping_service import FinancialScrapingService
        from services.crypto_scraping_service import CryptoScrapingService
        from services.world_events_service import WorldEventsService
        from services.foreign_markets_service import ForeignMarketsService
        from services.gov_filings_service import GovFilingsService
        from services.hypothesis_service import HypothesisService

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

        hypothesis_svc = HypothesisService(os.environ.get("EMERGENT_LLM_KEY"))
        hypothesis = await hypothesis_svc.generate_hypothesis(symbol, data)
        hypothesis["is_pro"] = True

        # Check for verdict change → generate notification
        if user and hypothesis.get("verdict"):
            try:
                prev = await db.hypothesis_history.find_one(
                    {"user_id": user["_id"], "symbol": symbol.upper()},
                    sort=[("searched_at", -1)]
                )
                if prev and prev.get("verdict") and prev["verdict"] != hypothesis["verdict"]:
                    wl = await db.watchlists.find_one({"user_id": user["_id"]})
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
