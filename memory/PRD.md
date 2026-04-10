# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Requires real market data, crypto & dark pool data, functional trading broker connections, a Stripe subscription gateway ($45/month), an AI chat assistant, and a highly complex AI market prediction engine that scrapes financial news, crypto transactions, and real estate data. Deep navy `#0F172A` background with blue `#0052FF` accents ("Fidelity × Coinbase" aesthetic).

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **Auth**: httpOnly secure cookies (JWT), 90s fetch timeout for AI endpoints
- **AI**: Emergent LLM Key (GPT-5.2, Claude Sonnet 4.5, Gemini)
- **Payments**: Stripe ($45/month Pro subscription)
- **Market Data**: Alpha Vantage (paid tier) with yfinance fallback, Finnhub
- **Email**: Resend

## Critical Technical Decisions
- **No AbortController in authFetch**: Causes postMessage clone errors with Service Workers
- **90s fetch timeout**: AI endpoints take 8-50s; do NOT lower below 90s
- **Dynamic API base URL**: `getApiBase()` in `/app/frontend/src/utils/apiBase.js` resolves to correct domain on any deployment
- **Auto-refresh JWT**: `authFetch` silently refreshes expired access tokens on 401
- **Dynamic CORS middleware**: Reflects request Origin for any deployed domain (risedual.ai, preview, etc.)
- **No crewai package**: Native thread-safe multi-agent engine (`crew_engine.py`) built using ThreadPoolExecutor + asyncio.to_thread
- **Unified Price Provider**: All market data fetching goes through `price_provider.py` (AV → yfinance → MongoDB cache). Never call Alpha Vantage directly.

## Core Features (All Implemented)
- Real-time stock & crypto tickers
- Options Radar, Dark Pool tables
- AI War Room (unified command center)
- AI Intelligence Hub (Score, Patterns, Quick Brief)
- AI Investment Hypothesis (multi-model consensus)
- Multimodal AI Chat (image upload, chart patterns)
- Perplexity-style Company Research
- Market Predictions (scrapes world events, foreign markets, gov filings)
- Sector Rotation Heatmap
- Realtime P&L Tracker
- 8 Mock Broker Integrations (Alpaca, Schwab, IBKR, MooMoo, Webull, Robinhood, Public, Kraken)
- Stripe Subscription Gateway ($45/month)
- Admin Panel with Cache Monitor
- Referral System with Promo Codes
- Trading Journal, Strategy Builder, Strategy Marketplace
- Prediction Accuracy Tracker (Pro feature)
- Native Multi-Agent AI Crew Engine (War Room, Hypothesis, Predictions)

## Price Provider Integration (April 10, 2026)
- Built `price_provider.py`: Smart routing AV → yfinance → MongoDB cache
- Integrated into ALL backend services (stocks + crypto):
  - `sector_service.py`, `war_room_service.py`, `prediction_tracker.py` (done by previous agent)
  - `ai_intelligence_service.py`, `watchlist_intelligence_service.py`, `company_research_service.py`, `market_data_service.py`, `backtester_service.py` (completed this session)
- Added crypto fallback: AV `CURRENCY_EXCHANGE_RATE` → yfinance `{TICKER}-USD` → MongoDB cache
- Fixed Motor Database boolean check bug (`if _db is not None:` instead of `if _db:`)
- Only remaining direct AV call: Earnings endpoint in war_room_service.py

## Deployment Status
- Health Check: PASSED
- CORS: Dynamic origin reflection for any domain
- API routing: Dynamic getApiBase() for any deployment
- All features verified via testing agent (100% pass)

## AI Sentiment Heatmap (April 10, 2026)
- Built multi-agent sector sentiment crew in `crew_definitions.py` (3 analysts + 1 strategist = 4 LLM calls for all 11 sectors)
- Added `GET /api/sectors/sentiment` endpoint in `routes/sectors.py` (15min cache TTL)
- Updated `SectorHeatmap.jsx` with AI tab: shows scores (0-100), labels (Bullish/Bearish/Cautious/Neutral), reasoning, rotation call, risk regime
- Color mapping: 0-25 red → 25-45 orange → 45-55 neutral → 55-75 green → 75-100 strong green

## Market Vector Memory System (April 10, 2026)
- Built `market_memory_service.py` using ChromaDB + all-MiniLM-L6-v2 (local embeddings, zero API cost)
- Stores past market regime episodes as vectors: price action, macro data, AI predictions, actual results
- Before each prediction, queries for top 3 similar historical regimes and injects context into AI prompt
- Auto-saves verified predictions (hits/misses) from `prediction_tracker.py` as new memory episodes
- New endpoint: `GET /api/accuracy/memory` (Pro only) returns memory stats
- Persistent storage at `/app/backend/data/chromadb`

### Memory Training (Bulk Bootstrap)
- Built `memory_training_service.py`: fetches 2yr daily data for 33 symbols (mega-cap stocks + sector/market ETFs)
- Calculates RSI (14), SMA(20/50) trend, volume signals every 5 trading days
- Tags each regime with actual 5-day forward outcome (hit/miss/neutral)
- **2,973 historical episodes** ingested from 33 symbols
- New endpoints: `POST /api/accuracy/memory/train` (triggers background task), `GET /api/accuracy/memory/train/status`

### Strategist Context — Win Pattern Injection (April 10, 2026)
- Added `get_strategist_context()` to `market_memory_service.py`: filters ChromaDB for `outcome='hit'` only
- All 3 AI crews (War Room, Hypothesis, Market Prediction) now inject win patterns into synthesizer prompts
- Format: "HISTORICAL WIN PATTERNS for {ticker} (RSI ~{n})" with similarity scores, dates, and actual outcomes
- Graceful fallback: returns generic patterns if no ticker-specific wins exist

### Enriched Regime Format (April 10, 2026)
- Added `market_sentiment_service.py`: Fear & Greed Index (Alternative.me API, free) + VIX level (yfinance)
- Regime snapshots now include structured `{metrics: {rsi, vol_delta, change_1d, trend}, sentiment: {fg_index, fg_label}}`
- Training fetches 730 days of historical F&G data and tags each snapshot by date
- Volume delta: % above/below 20-day average volume
- New endpoint: `GET /api/sentiment/fear-greed` returns live F&G + VIX

## Broker OAuth 2.0 (April 10, 2026)
- Added OAuth 2.0 authorization flow for Alpaca (extensible to other brokers)
- New endpoints: `GET /api/broker/oauth/{broker_id}/status`, `/authorize`, `/callback`
- Frontend: OAuth button shown when configured, URL param callback handling, auth method badge
- `AlpacaTradingService` supports both API key and OAuth bearer token authentication
- CSRF-protected via `oauth_states` collection with one-time state tokens
- To enable: Set `ALPACA_OAUTH_CLIENT_ID` and `ALPACA_OAUTH_CLIENT_SECRET` in backend/.env

## Historical Sentiment Tracking (April 10, 2026)
- Every AI sentiment run is auto-logged to `sentiment_history` collection in MongoDB
- New endpoint: `GET /api/sectors/sentiment/history?limit=20` returns per-sector trend timeseries
- Frontend: Sparkline SVGs show sentiment trend per sector tile, snapshot count badge in header

## Known Limitations
- Broker integrations are mocked (no real OAuth flows)
- Finnhub Congressional Trading API returns 403 on free tier (gracefully handled)
- Crypto prices may return empty when AV rate-limited (no yfinance fallback for crypto exchange rates)

## Backlog
- P1: AI Sentiment Heatmap — Update Sector Heatmap colors to reflect AI sentiment scores
- P2: Broker OAuth — Replace mocked broker endpoints with real authenticated flows
- P3: Verify risedual.ai production deployment
- P4: Refactor server.py into separate route modules
