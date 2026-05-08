# TradealgoGPT - API Contracts & Integration Plan

## 1. API Endpoints

### Stock & Market Data

#### GET /api/stocks/ticker
**Purpose:** Get real-time stock ticker data for top stocks
**Response:**
```json
[
  {
    "symbol": "SPY",
    "price": 572.38,
    "change": -8.93,
    "changePercent": -1.31
  }
]
```

#### GET /api/stocks/quote/:symbol
**Purpose:** Get detailed quote for a specific stock
**Response:**
```json
{
  "symbol": "AAPL",
  "price": 175.50,
  "change": 2.30,
  "changePercent": 1.33,
  "volume": 65000000,
  "high": 176.20,
  "low": 173.80
}
```

### Options Data

#### GET /api/options/radar
**Purpose:** Get AI-powered options radar data
**Response:**
```json
{
  "mostActivelyTraded": [...],
  "volatilityOpportunities": [...]
}
```

#### GET /api/options/flow
**Purpose:** Get options flow screener data
**Response:**
```json
{
  "mostActivelyTraded": [...],
  "dteEdge": [...],
  "volatilityLow": [...],
  "volatilityHigh": [...]
}
```

#### GET /api/options/momentum
**Purpose:** Get momentum close strength data

#### GET /api/options/fast-movers
**Purpose:** Get fast mover calls data

#### GET /api/options/unusual-volume
**Purpose:** Get unusual options volume data

### AI Chat

#### POST /api/chat
**Purpose:** TradeGPT AI chat interface
**Request:**
```json
{
  "message": "What's the outlook for AAPL?",
  "sessionId": "uuid"
}
```
**Response:**
```json
{
  "response": "Based on current market data...",
  "sessionId": "uuid"
}
```

#### GET /api/chat/history/:sessionId
**Purpose:** Get chat history for a session

## 2. Mock Data to Replace

### Frontend Files:
- `/app/frontend/src/mockData.js` - All mock data will be replaced with API calls

### Components to Update:
- `StockTicker.jsx` - Fetch from `/api/stocks/ticker`
- `OptionsRadar.jsx` - Fetch from `/api/options/radar`
- `OptionsFlowScreener.jsx` - Fetch from `/api/options/flow`
- `AdditionalSections.jsx` - Fetch from `/api/options/momentum`, `/api/options/fast-movers`, `/api/options/unusual-volume`
- `TradeGPTChat.jsx` - Connect to `/api/chat`

## 3. Backend Implementation Plan

### External APIs:
1. **Alpha Vantage** (API Key: 1FW6ZOF9JGQRSMPX)
   - Real-time stock quotes
   - Intraday data
   - Options data (if available)

2. **Emergent LLM** (for TradeGPT)
   - Chat completions with GPT model
   - Context-aware trading assistant

### Database Models:

#### ChatSession
```python
{
  "session_id": str,
  "messages": List[{
    "role": str,  # "user" or "assistant"
    "content": str,
    "timestamp": datetime
  }],
  "created_at": datetime
}
```

#### StockCache
```python
{
  "symbol": str,
  "data": dict,
  "last_updated": datetime
}
```

### Backend Services:
1. `market_data_service.py` - Alpha Vantage API integration
2. `ai_service.py` - Emergent LLM integration for TradeGPT
3. `cache_service.py` - Caching to avoid API rate limits

## 4. Frontend-Backend Integration

### Update API Base URL:
- Already configured in `/app/frontend/.env` as `REACT_APP_BACKEND_URL`

### Add axios calls in components:
1. Create `/app/frontend/src/services/api.js` with all API functions
2. Update components to use API service instead of mockData
3. Add loading states and error handling
4. Implement auto-refresh for real-time data

### Real-time Updates:
- Stock ticker: Update every 30 seconds
- Options data: Update every 2 minutes (API rate limit consideration)
- Chat: Real-time request/response

## 5. Implementation Steps

1. ✅ Frontend with mock data
2. ⏳ Backend services setup:
   - Alpha Vantage integration
   - Emergent LLM integration
   - Database models
3. ⏳ API endpoints implementation
4. ⏳ Frontend integration with backend
5. ⏳ Testing and optimization
