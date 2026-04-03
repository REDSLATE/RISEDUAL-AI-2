from fastapi import FastAPI, APIRouter, HTTPException, Request
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Optional
import uuid
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Import services
from services.market_data_service import MarketDataService
from services.ai_service import AIService
from models.chat import ChatRequest, ChatResponse, ChatSession, ChatMessage
from routes.auth import auth_router, set_db as set_auth_db, seed_admin, create_indexes, get_current_user, get_optional_user

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Initialize services
market_service = MarketDataService()
ai_service = AIService()

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# Define Models
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Ignore MongoDB's _id field
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StatusCheckCreate(BaseModel):
    client_name: str

# Add your routes to the router instead of directly to app
@api_router.get("/")
async def root():
    return {"message": "RISEDUALAI API - Ready"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)
    
    # Convert to dict and serialize datetime to ISO string for MongoDB
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    
    _ = await db.status_checks.insert_one(doc)
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    # Exclude MongoDB's _id field from the query results
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    
    # Convert ISO string timestamps back to datetime objects
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    
    return status_checks

# Stock Market Data Endpoints
@api_router.get("/stocks/ticker")
async def get_ticker():
    """Get real-time ticker data for top stocks"""
    try:
        ticker_data = await market_service.get_ticker_data()
        return ticker_data
    except Exception as e:
        logging.error(f"Error fetching ticker data: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching ticker data")

@api_router.get("/stocks/quote/{symbol}")
async def get_quote(symbol: str):
    """Get real-time quote for a specific stock"""
    try:
        quote = await market_service.get_quote(symbol)
        if not quote:
            raise HTTPException(status_code=404, detail=f"Quote not found for {symbol}")
        return quote
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching quote for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching quote")

# Options Data Endpoints
@api_router.get("/options/radar")
async def get_options_radar():
    """Get AI Options Radar data"""
    try:
        return market_service.generate_mock_options_data('radar')
    except Exception as e:
        logging.error(f"Error fetching options radar: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching options radar")

@api_router.get("/options/flow")
async def get_options_flow():
    """Get Options Flow Screener data"""
    try:
        return market_service.generate_mock_options_data('flow')
    except Exception as e:
        logging.error(f"Error fetching options flow: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching options flow")

@api_router.get("/options/momentum")
async def get_momentum():
    """Get Momentum Close Strength data"""
    try:
        return market_service.generate_mock_options_data('momentum')
    except Exception as e:
        logging.error(f"Error fetching momentum data: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching momentum data")

@api_router.get("/options/fast-movers")
async def get_fast_movers():
    """Get Fast Mover Calls data"""
    try:
        return market_service.generate_mock_options_data('fast_movers')
    except Exception as e:
        logging.error(f"Error fetching fast movers: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching fast movers")

@api_router.get("/options/unusual-volume")
async def get_unusual_volume():
    """Get Unusual Options Volume data"""
    try:
        return market_service.generate_mock_options_data('unusual_volume')
    except Exception as e:
        logging.error(f"Error fetching unusual volume: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching unusual volume")

# AI Chat Endpoints
@api_router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """TradeGPT AI chat endpoint"""
    try:
        # Get or create chat session
        session = await db.chat_sessions.find_one({"session_id": request.sessionId})
        
        if not session:
            # Create new session
            new_session = ChatSession(session_id=request.sessionId)
            await db.chat_sessions.insert_one(new_session.dict())
        
        # Get AI response (with optional image)
        ai_response = await ai_service.chat(request.message, request.sessionId, request.image_base64)
        
        # Save messages to database (don't store full base64 in DB, just a flag)
        user_message = ChatMessage(
            role="user",
            content=request.message,
            image_base64="[image_attached]" if request.image_base64 else None
        )
        assistant_message = ChatMessage(role="assistant", content=ai_response)
        
        await db.chat_sessions.update_one(
            {"session_id": request.sessionId},
            {
                "$push": {
                    "messages": {
                        "$each": [user_message.dict(), assistant_message.dict()]
                    }
                },
                "$set": {"updated_at": datetime.now(timezone.utc)}
            }
        )
        
        return ChatResponse(response=ai_response, sessionId=request.sessionId)
        
    except Exception as e:
        logging.error(f"Error in chat endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail="Error processing chat request")

@api_router.get("/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    """Get chat history for a session"""
    try:
        session = await db.chat_sessions.find_one({"session_id": session_id})
        if not session:
            return {"messages": []}
        return {"messages": session.get("messages", [])}
    except Exception as e:
        logging.error(f"Error fetching chat history: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching chat history")

# Crypto Data Endpoints
@api_router.get("/crypto/prices")
async def get_crypto_prices():
    """Get top crypto currencies data"""
    try:
        crypto_data = await market_service.get_crypto_data()
        return crypto_data
    except Exception as e:
        logging.error(f"Error fetching crypto data: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching crypto data")

@api_router.get("/crypto/{symbol}")
async def get_crypto_by_symbol(symbol: str):
    """Get specific crypto currency data"""
    try:
        crypto = await market_service.get_crypto_quote(symbol)
        if not crypto:
            raise HTTPException(status_code=404, detail=f"Crypto not found for {symbol}")
        return crypto
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching crypto {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching crypto")

# Dark Pool Data Endpoints
@api_router.get("/dark-pool")
async def get_dark_pool():
    """Get dark pool trading data"""
    try:
        dark_pool_data = market_service.generate_dark_pool_data()
        return dark_pool_data
    except Exception as e:
        logging.error(f"Error fetching dark pool data: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching dark pool data")

# Trading & Broker Integration Endpoints
@api_router.post("/broker/connect")
async def connect_broker(broker_id: str, credentials: Dict):
    """Connect to a broker (requires API credentials)"""
    try:
        from services.broker_service import BrokerService
        
        # Validate credentials by testing connection
        client = BrokerService.get_broker_client(broker_id, credentials)
        account = client.get_account()
        
        if not account:
            raise HTTPException(status_code=400, detail="Failed to connect to broker")
        
        # In production, store encrypted credentials in database
        return {
            "status": "connected",
            "broker_id": broker_id,
            "account_id": account.get('account_number', 'N/A'),
            "message": "Successfully connected to broker"
        }
    except Exception as e:
        logging.error(f"Error connecting to broker: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# Company Research Endpoints (Perplexity-style)
@api_router.get("/research/{symbol}")
async def research_company(symbol: str):
    """Perplexity-style company research with AI synthesis"""
    try:
        from services.company_research_service import CompanyResearchService
        research_service = CompanyResearchService()
        session_id = f"research_{symbol}_{datetime.now(timezone.utc).isoformat()}"
        result = await research_service.research_company(symbol, session_id)
        return result
    except Exception as e:
        logging.error(f"Error researching {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# World Events & Geopolitical Impact
@api_router.get("/world-events")
async def get_world_events():
    """Scrape world events and map to affected sectors/companies"""
    try:
        from services.world_events_service import WorldEventsService
        service = WorldEventsService()
        return await service.scrape_world_events()
    except Exception as e:
        logging.error(f"Error fetching world events: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Foreign Markets & Commodities
@api_router.get("/foreign-markets")
async def get_foreign_markets():
    """Get international market indices, commodities, and currencies"""
    try:
        from services.foreign_markets_service import ForeignMarketsService
        service = ForeignMarketsService()
        return await service.get_foreign_markets()
    except Exception as e:
        logging.error(f"Error fetching foreign markets: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Government & Regulatory Filings
@api_router.get("/gov-filings")
async def get_gov_filings():
    """Get SEC insider trades, Fed announcements, and Congressional trades"""
    try:
        from services.gov_filings_service import GovFilingsService
        service = GovFilingsService()
        return await service.get_all_gov_data()
    except Exception as e:
        logging.error(f"Error fetching gov filings: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Subscription & Payment Endpoints
class CheckoutRequest(BaseModel):
    origin_url: str
    plan: str = "monthly"

@api_router.post("/subscription/create-checkout-session")
async def create_checkout_session(request: CheckoutRequest, http_request: Request):
    """Create Stripe checkout session for monthly or annual subscription"""
    try:
        from services.payment_service import StripePaymentService

        payment_service = StripePaymentService()
        host_url = str(http_request.base_url).rstrip('/')
        webhook_url = f"{host_url}/api/webhook/stripe"

        plan = request.plan if request.plan in ("monthly", "annual") else "monthly"
        amount = 486.00 if plan == "annual" else 45.00

        metadata = {
            "plan": f"risedualai_pro_{plan}",
            "source": "web_checkout"
        }

        session = await payment_service.create_checkout_session(
            origin_url=request.origin_url,
            webhook_url=webhook_url,
            metadata=metadata,
            plan=plan
        )

        # Record pending transaction in DB
        await db.payment_transactions.insert_one({
            "session_id": session.session_id,
            "amount": amount,
            "currency": "usd",
            "plan": f"risedualai_pro_{plan}",
            "metadata": metadata,
            "payment_status": "initiated",
            "created_at": datetime.now(timezone.utc).isoformat()
        })

        return {"url": session.url, "session_id": session.session_id}

    except Exception as e:
        logging.error(f"Error creating checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/subscription/status/{session_id}")
async def get_payment_status(session_id: str, http_request: Request):
    """Poll payment status for a checkout session"""
    try:
        from services.payment_service import StripePaymentService

        payment_service = StripePaymentService()
        host_url = str(http_request.base_url).rstrip('/')
        webhook_url = f"{host_url}/api/webhook/stripe"

        status = await payment_service.get_checkout_status(session_id, webhook_url)

        # Update transaction in DB (only once)
        existing = await db.payment_transactions.find_one({"session_id": session_id})
        if existing and existing.get("payment_status") != status.payment_status:
            await db.payment_transactions.update_one(
                {"session_id": session_id},
                {"$set": {
                    "payment_status": status.payment_status,
                    "status": status.status,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )

        return {
            "status": status.status,
            "payment_status": status.payment_status,
            "amount_total": status.amount_total,
            "currency": status.currency
        }

    except Exception as e:
        logging.error(f"Error fetching payment status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    try:
        from services.payment_service import StripePaymentService

        payment_service = StripePaymentService()
        body = await request.body()
        signature = request.headers.get("Stripe-Signature", "")
        host_url = str(request.base_url).rstrip('/')
        webhook_url = f"{host_url}/api/webhook/stripe"

        webhook_response = await payment_service.handle_webhook(body, signature, webhook_url)

        if webhook_response and webhook_response.session_id:
            await db.payment_transactions.update_one(
                {"session_id": webhook_response.session_id},
                {"$set": {
                    "payment_status": webhook_response.payment_status,
                    "event_type": webhook_response.event_type,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )

        return {"status": "ok"}

    except Exception as e:
        logging.error(f"Webhook error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

# Market Prediction & Scraping Endpoints
@api_router.get("/market/prediction")
async def get_market_prediction():
    """Get AI-powered market prediction based on scraped data + macro sources"""
    try:
        from services.financial_scraping_service import FinancialScrapingService
        from services.crypto_scraping_service import CryptoScrapingService
        from services.real_estate_scraping_service import RealEstateScrapingService
        from services.market_prediction_service import MarketPredictionService
        from services.world_events_service import WorldEventsService
        from services.foreign_markets_service import ForeignMarketsService
        from services.gov_filings_service import GovFilingsService
        
        # Gather data from all sources
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
        
        # NEW: Macro data sources
        world_events = await world_events_svc.scrape_world_events()
        foreign_markets = await foreign_markets_svc.get_foreign_markets()
        gov_filings = await gov_filings_svc.get_all_gov_data()
        
        # Combine crypto data
        all_crypto_data = crypto_data + [crypto_sentiment] + whale_transactions
        
        # Get AI prediction with all data sources
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
        
        # Add real estate summary to prediction
        prediction['real_estate_summary'] = {
            'housing_health': real_estate_data.get('housing', {}).get('market_health', 'unknown'),
            'commercial_trend': 'mixed',
            'data_sources': len(real_estate_data.get('housing', {}).get('sources', [])),
            'implications': real_estate_data.get('trends', {}).get('market_implications', {})
        }
        
        # Add macro data summaries
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
        logging.error(f"Error generating prediction: {str(e)}")
        raise HTTPException(status_code=500, detail="Error generating market prediction")

@api_router.get("/hypothesis/{symbol}")
async def get_hypothesis(symbol: str, request: Request):
    """Get AI hypothesis for a specific ticker. Requires Pro subscription."""
    user = await get_optional_user(request)
    
    # Check subscription status
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
        
        # Gather all data
        news = await financial_scraper.scrape_financial_news()
        social = await financial_scraper.scrape_reddit_sentiment()
        crypto_data = await crypto_scraper.get_exchange_data()
        world_events = await world_events_svc.scrape_world_events()
        foreign_markets = await foreign_markets_svc.get_foreign_markets()
        gov_filings = await gov_filings_svc.get_all_gov_data()
        
        data = {
            "news": news,
            "social": social,
            "crypto": crypto_data,
            "world_events": world_events,
            "foreign_markets": foreign_markets,
            "gov_filings": gov_filings,
        }
        
        if not is_pro:
            # Return teaser for free users
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
        
        # Pro user: generate full hypothesis
        hypothesis_svc = HypothesisService(os.environ.get("EMERGENT_LLM_KEY"))
        hypothesis = await hypothesis_svc.generate_hypothesis(symbol, data)
        hypothesis["is_pro"] = True
        return hypothesis
        
    except Exception as e:
        logging.error(f"Error generating hypothesis for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error generating hypothesis")

@api_router.post("/user/subscription")
async def update_subscription(request: Request):
    """Update user subscription status after payment"""
    user = await get_current_user(request)
    body = await request.json()
    status = body.get("status", "pro")
    plan = body.get("plan", "monthly")
    await db.users.update_one(
        {"_id": __import__('bson').ObjectId(user["_id"])},
        {"$set": {
            "subscription_status": status,
            "subscription_plan": plan,
            "subscription_updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    return {"message": "Subscription updated", "status": status}


@api_router.get("/market/news")
async def get_financial_news():
    """Get latest financial news from multiple sources"""
    try:
        from services.financial_scraping_service import FinancialScrapingService
        scraper = FinancialScrapingService()
        news = await scraper.scrape_financial_news()
        return news
    except Exception as e:
        logging.error(f"Error fetching news: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching news")

@api_router.get("/market/social-sentiment")
async def get_social_sentiment():
    """Get social media sentiment from Reddit WallStreetBets"""
    try:
        from services.financial_scraping_service import FinancialScrapingService
        scraper = FinancialScrapingService()
        sentiment = await scraper.scrape_reddit_sentiment()
        return sentiment
    except Exception as e:
        logging.error(f"Error fetching social sentiment: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching social sentiment")

@api_router.get("/market/insider-trades")
async def get_insider_trades():
    """Get recent insider trading activity"""
    try:
        from services.financial_scraping_service import FinancialScrapingService
        scraper = FinancialScrapingService()
        trades = await scraper.scrape_insider_trades()
        return trades
    except Exception as e:
        logging.error(f"Error fetching insider trades: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching insider trades")

@api_router.get("/market/crypto-data")
async def get_crypto_market_data():
    """Get crypto market data from exchanges"""
    try:
        from services.crypto_scraping_service import CryptoScrapingService
        scraper = CryptoScrapingService()
        
        exchange_data = await scraper.get_exchange_data()
        whale_transactions = await scraper.get_whale_transactions()
        crypto_sentiment = await scraper.get_crypto_sentiment()
        
        return {
            'exchange_data': exchange_data,
            'whale_transactions': whale_transactions,
            'sentiment': crypto_sentiment
        }
    except Exception as e:
        logging.error(f"Error fetching crypto data: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching crypto data")

@api_router.get("/market/real-estate")
async def get_real_estate_data():
    """Get housing and commercial real estate market data"""
    try:
        from services.real_estate_scraping_service import RealEstateScrapingService
        scraper = RealEstateScrapingService()
        
        real_estate_data = await scraper.scrape_all_real_estate_data()
        return real_estate_data
    except Exception as e:
        logging.error(f"Error fetching real estate data: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching real estate data")

@api_router.get("/trading/account/{broker_id}")
async def get_account_info(broker_id: str):
    """Get account balance and information"""
    try:
        from services.broker_service import BrokerService
        
        # In production, retrieve credentials from database
        credentials = {
            'api_key': os.environ.get('ALPACA_API_KEY'),
            'api_secret': os.environ.get('ALPACA_API_SECRET'),
            'paper': os.environ.get('ALPACA_PAPER_TRADING', 'true').lower() == 'true'
        }
        
        client = BrokerService.get_broker_client(broker_id, credentials)
        account = client.get_account()
        
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        return {
            "broker": broker_id,
            "account_id": account.get('account_number'),
            "cash": float(account.get('cash', 0)),
            "buying_power": float(account.get('buying_power', 0)),
            "portfolio_value": float(account.get('portfolio_value', 0)),
            "equity": float(account.get('equity', 0))
        }
    except Exception as e:
        logging.error(f"Error fetching account: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching account")

@api_router.get("/trading/positions/{broker_id}")
async def get_positions(broker_id: str):
    """Get all open positions"""
    try:
        from services.broker_service import BrokerService
        
        credentials = {
            'api_key': os.environ.get('ALPACA_API_KEY'),
            'api_secret': os.environ.get('ALPACA_API_SECRET'),
            'paper': os.environ.get('ALPACA_PAPER_TRADING', 'true').lower() == 'true'
        }
        
        client = BrokerService.get_broker_client(broker_id, credentials)
        positions = client.get_positions()
        
        return positions
    except Exception as e:
        logging.error(f"Error fetching positions: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching positions")

@api_router.post("/trading/order/{broker_id}")
async def place_order(broker_id: str, order: Dict):
    """Place a trading order"""
    try:
        from services.broker_service import BrokerService
        
        credentials = {
            'api_key': os.environ.get('ALPACA_API_KEY'),
            'api_secret': os.environ.get('ALPACA_API_SECRET'),
            'paper': os.environ.get('ALPACA_PAPER_TRADING', 'true').lower() == 'true'
        }
        
        client = BrokerService.get_broker_client(broker_id, credentials)
        
        result = client.place_order(
            symbol=order.get('symbol'),
            qty=order.get('quantity'),
            side=order.get('side'),
            order_type=order.get('type', 'market'),
            time_in_force=order.get('time_in_force', 'day'),
            limit_price=order.get('limit_price'),
            stop_price=order.get('stop_price')
        )
        
        if not result:
            raise HTTPException(status_code=400, detail="Failed to place order")
        
        return result
    except Exception as e:
        logging.error(f"Error placing order: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/trading/orders/{broker_id}")
async def get_orders(broker_id: str, status: str = 'all'):
    """Get all orders"""
    try:
        from services.broker_service import BrokerService
        
        credentials = {
            'api_key': os.environ.get('ALPACA_API_KEY'),
            'api_secret': os.environ.get('ALPACA_API_SECRET'),
            'paper': os.environ.get('ALPACA_PAPER_TRADING', 'true').lower() == 'true'
        }
        
        client = BrokerService.get_broker_client(broker_id, credentials)
        orders = client.get_orders(status=status)
        
        return orders
    except Exception as e:
        logging.error(f"Error fetching orders: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching orders")

@api_router.delete("/trading/order/{broker_id}/{order_id}")
async def cancel_order(broker_id: str, order_id: str):
    """Cancel an order"""
    try:
        from services.broker_service import BrokerService
        
        credentials = {
            'api_key': os.environ.get('ALPACA_API_KEY'),
            'api_secret': os.environ.get('ALPACA_API_SECRET'),
            'paper': os.environ.get('ALPACA_PAPER_TRADING', 'true').lower() == 'true'
        }
        
        client = BrokerService.get_broker_client(broker_id, credentials)
        success = client.cancel_order(order_id)
        
        if not success:
            raise HTTPException(status_code=400, detail="Failed to cancel order")
        
        return {"status": "cancelled", "order_id": order_id}
    except Exception as e:
        logging.error(f"Error cancelling order: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Include the router in the main app
app.include_router(api_router)
app.include_router(auth_router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("startup")
async def startup_event():
    set_auth_db(db)
    await create_indexes()
    await seed_admin()
    # Write test credentials
    creds_path = Path("/app/memory/test_credentials.md")
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text(
        "# Test Credentials\n\n"
        f"## Admin\n- Email: {os.environ.get('ADMIN_EMAIL', 'admin@risedual.ai')}\n"
        f"- Password: {os.environ.get('ADMIN_PASSWORD', 'RiseDual2026!')}\n"
        "- Role: admin\n- Subscription: pro\n\n"
        "## Auth Endpoints\n"
        "- POST /api/auth/register\n- POST /api/auth/login\n- POST /api/auth/logout\n"
        "- GET /api/auth/me\n- POST /api/auth/refresh\n"
    )

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()