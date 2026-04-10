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

## Known Limitations
- Broker integrations are mocked (no real OAuth flows)
- Finnhub Congressional Trading API returns 403 on free tier (gracefully handled)
- Crypto prices may return empty when AV rate-limited (no yfinance fallback for crypto exchange rates)

## Backlog
- P1: AI Sentiment Heatmap — Update Sector Heatmap colors to reflect AI sentiment scores
- P2: Broker OAuth — Replace mocked broker endpoints with real authenticated flows
- P3: Verify risedual.ai production deployment
- P4: Refactor server.py into separate route modules
