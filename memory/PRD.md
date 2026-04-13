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
- **Dynamic API base URL**: `getApiBase()` in `/app/frontend/src/utils/apiBase.js`
- **Auto-refresh JWT**: `authFetch` silently refreshes expired access tokens on 401
- **Dynamic CORS middleware**: Reflects request Origin for any deployed domain
- **No crewai package**: Native thread-safe multi-agent engine (`crew_engine.py`)
- **Unified Price Provider**: `price_provider.py` (AV → yfinance → MongoDB cache)
- **Motor DB Boolean Checks**: NEVER use `if db:`. ALWAYS use `if db is not None:`

## Core Features (All Implemented)
- Real-time stock & crypto tickers, Options Radar, Dark Pool tables
- AI War Room, AI Intelligence Hub, AI Investment Hypothesis (multi-model consensus)
- Multimodal AI Chat (image upload, chart patterns), Voice Chat (TTS + STT)
- Perplexity-style Company Research, Market Predictions
- Sector Rotation Heatmap + AI Sentiment, Realtime P&L Tracker
- 8 Mock Broker Integrations, Stripe Subscription Gateway ($45/month)
- Admin Panel with Cache Monitor + Security Audit + Success Fees
- Referral System, Trading Journal, Strategy Builder, Strategy Marketplace
- Prediction Accuracy Tracker, Native Multi-Agent AI Crew Engine
- Market Vector Memory System (ChromaDB), VAPID Web Push Whale Alerts
- Live Order Flow Heatmaps, Multi-Ticker Whale Radar (10 crypto pairs SSE)
- Paper Trading ($100K simulated), Portfolio Agent with AI Tool Calling
- Broker API Key Vault + Role-Based Execution, 3-Legged OAuth + PKCE
- Legal Pages (Terms, Privacy, Risk Disclosure, Disclaimer)
- Media Upload / Object Storage, SEO Optimization
- Persistent Chat Memory (Pro-only, manual pinning)
- Ticker Search for Market Predictions, Beta Waitlist System
- User Badges (Creator, Founding 100, Beta, Pro)
- Waitlist Analytics Dashboard
- Smart Orders System (Ladder, Trailing SL/TP, Break-Even)
- Risk/Reward Calculator (% Risk, Fixed Dollar, Kelly Criterion)
- Market Scanner (10 pre-built strategies + Visual Rule Builder + AI Validation)
- Trading Bots (Grid, Signal, Webhook — all default OFF)
- Bots Dashboard + Help Center (45+ topics), Contextual Tooltips
- Interactive Onboarding Tour (10-step guided walkthrough)

### Success Fee System (April 13, 2026)
- **Fee structure**: 1.5% on monthly gains above $1,000 for broker-connected users
- **Gains only**: Zero fee on losses, breakeven, or gains below threshold
- **Monthly billing**: Resets on 1st of each month. Starting balance captured from portfolio snapshots.
- **Manual collection**: Admin marks fees as paid/waived via Admin Panel
- **Backend**: `success_fee_service.py` + `routes/success_fee.py` (6 endpoints)
  - `GET /api/success-fee/current` — current month fee summary
  - `GET /api/success-fee/history` — past months' records
  - `GET /api/success-fee/admin/stats` — aggregate stats (admin)
  - `GET /api/success-fee/admin/list` — all user fees (admin)
  - `POST /api/success-fee/admin/mark-paid` — mark as paid (admin)
  - `POST /api/success-fee/admin/waive` — waive fee (admin)
- **Frontend**: `SuccessFeeWidget.jsx` (dashboard card with P&L, threshold bar, history)
  - `admin/SuccessFeesTab.jsx` (stats, filters, mark-paid/waive actions)
- **Legal**: Terms of Service Section 6 added, Landing page pricing + FAQ updated
- **MongoDB collection**: `success_fees`
- **Verified (Iteration 116)**: 96% backend (22/23), 100% frontend

## Code Architecture
```
/app
├── frontend/src/components/
│   ├── scanner/, admin/, heatmap/, chat/
│   ├── SuccessFeeWidget.jsx, SmartOrderPanel.jsx, RiskCalculator.jsx
│   ├── TradingBotPanel.jsx, BotsDashboard.jsx
│   ├── HelpCenter.jsx, OnboardingTour.jsx, ModalManager.jsx
├── backend/
│   ├── services/
│   │   ├── success_fee_service.py, smart_order_service.py, scanner_service.py
│   │   ├── trading_bot_service.py, market_memory_service.py, price_provider.py
│   ├── routes/
│   │   ├── success_fee.py, smart_orders.py, scanner.py, trading_bots.py
│   ├── route_registry.py
```

## Known Limitations
- Broker integrations are mocked (no real OAuth flows)
- Finnhub Congressional Trading API returns 403 on free tier
- Binance global geo-blocked from some regions; uses Binance US as primary

## Backlog
- P1: Badge showcase on public user profiles & Leaderboard with badge visibility
- P1: Deploy to `risedual.ai` custom domain
- P2: Alpha Vantage API upgrade guidance
