# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform. Requires real market data, crypto & dark pool data, functional trading broker connections, a Stripe subscription gateway ($45/month), an AI chat assistant, and a highly complex AI market prediction engine that scrapes financial news, crypto transactions, and real estate data. Deep navy `#0F172A` background with blue `#0052FF` accents ("Fidelity × Coinbase" aesthetic).

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
- **Motor DB Boolean Checks**: NEVER use `if db:` or `if db and user:`. Motor DB objects raise an exception on truth testing. ALWAYS use `if db is not None:`.

## Core Features (All Implemented)
- Real-time stock & crypto tickers
- Options Radar, Dark Pool tables
- AI War Room (unified command center)
- AI Intelligence Hub (Score, Patterns, Quick Brief)
- AI Investment Hypothesis (multi-model consensus)
- Multimodal AI Chat (image upload, chart patterns)
- Perplexity-style Company Research
- Market Predictions (scrapes world events, foreign markets, gov filings)
- Sector Rotation Heatmap + AI Sentiment
- Realtime P&L Tracker
- 8 Mock Broker Integrations (Alpaca, Schwab, IBKR, MooMoo, Webull, Robinhood, Public, Kraken)
- Stripe Subscription Gateway ($45/month)
- Admin Panel with Cache Monitor + Security Audit
- Referral System with Promo Codes
- Trading Journal, Strategy Builder, Strategy Marketplace
- Prediction Accuracy Tracker (Pro feature)
- Native Multi-Agent AI Crew Engine (War Room, Hypothesis, Predictions)
- Market Vector Memory System (ChromaDB)
- VAPID Web Push Whale Alerts
- Live Order Flow Heatmaps (Binance L2 + yfinance)
- Multi-Ticker Whale Radar (10 crypto pairs SSE)
- Paper Trading ($100K simulated)
- Portfolio Agent with AI Tool Calling (GPT-5.2 function calling)
- Voice Chat (TTS + STT via OpenAI/Whisper)
- Broker API Key Vault + Role-Based Execution
- 3-Legged OAuth + PKCE + Refresh Token Rotation
- Legal Pages (Terms, Privacy, Risk Disclosure, Disclaimer)
- Media Upload / Object Storage System
- SEO Optimization (meta, OG, JSON-LD, sitemap, robots.txt)
- Persistent Chat Memory (Pro-only, manual pinning)
- Ticker Search for Market Predictions
- Beta Waitlist System (priority scoring, referrals, auto-invite cron, embeddable widget)
- Beta Key Redemption
- User Badges (Creator, Founding 100, Beta, Pro)
- Waitlist Analytics Dashboard
- Smart Orders System (Ladder, Trailing SL/TP, Break-Even)
- Risk/Reward Calculator (% Risk, Fixed Dollar, Kelly Criterion)
- Market Scanner (10 pre-built strategies + Visual Rule Builder with 23 indicators + AI Validation)
- Trading Bots (Grid, Signal, Webhook — all default OFF)
- Bots Dashboard + Help Center (45+ topics)
- Contextual Tooltips
- Interactive Onboarding Tour (10-step guided walkthrough, auto-trigger on first login)

## Code Architecture
```
/app
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── scanner/ (MarketScanner, RuleBuilder, ValidationResults)
│   │   │   ├── admin/ (WaitlistAnalytics, SecurityAudit, UsersTab)
│   │   │   ├── heatmap/ (HeatmapHeader, HeatmapLegend, SectorTile)
│   │   │   ├── chat/ (ChatHeader, MemoryPanel, ChatHistorySidebar)
│   │   │   ├── SmartOrderPanel.jsx, RiskCalculator.jsx
│   │   │   ├── TradingBotPanel.jsx, BotsDashboard.jsx
│   │   │   ├── HelpCenter.jsx, InfoTooltip.jsx, OnboardingTour.jsx
│   │   │   ├── ModalManager.jsx, MobileMenu.jsx
├── backend/
│   ├── services/
│   │   ├── smart_order_service.py, scanner_service.py
│   │   ├── ai_signal_validator.py, trading_bot_service.py
│   │   ├── market_memory_service.py, post_mortem_service.py
│   │   ├── chat_memory_service.py, price_provider.py
│   ├── routes/
│   │   ├── smart_orders.py, scanner.py, trading_bots.py, risk_calculator.py
│   │   ├── security_audit.py, stream.py, whale_radar.py
│   ├── route_registry.py
```

## Known Limitations
- Broker integrations are mocked (no real OAuth flows)
- Finnhub Congressional Trading API returns 403 on free tier (gracefully handled)
- Binance global (api.binance.com) geo-blocked from some cloud regions; uses Binance US as primary
- Crypto prices may return empty when AV rate-limited

## Backlog
- P1: Badge showcase on public user profiles & Leaderboard with badge visibility
- P1: Deploy to `risedual.ai` custom domain
- P2: Alpha Vantage API upgrade guidance
