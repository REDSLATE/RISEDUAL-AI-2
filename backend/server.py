from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict
import uuid
from datetime import datetime, timezone

# Import services
from services.market_data_service import MarketDataService
from services.ai_service import AIService
from models.chat import ChatRequest, ChatResponse, ChatSession, ChatMessage


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

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
        
        # Get AI response
        ai_response = await ai_service.chat(request.message, request.sessionId)
        
        # Save messages to database
        user_message = ChatMessage(role="user", content=request.message)
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

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()