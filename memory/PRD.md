# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT (TradeAlgo) named RISEDUAL AI with real market data, crypto & dark pool data, functional trading broker connections, Stripe subscriptions, an AI chat assistant, and a highly complex AI market prediction engine.

## Core Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (PWA, "Fidelity x Coinbase" aesthetic)
- **Backend**: FastAPI + MongoDB (Motor Async) + APScheduler
- **Integrations**: Emergent LLM (GPT-5.2, Claude 4.5, Gemini), Stripe, Finnhub, Alpha Vantage, Resend
- **Deployment**: Docker + docker-compose (Nginx + FastAPI + MongoDB)

## What's Been Implemented
- Real-time stock/crypto tickers with Alpha Vantage
- Options Radar, Dark Pool tables
- Multimodal AI Chat (image upload + chart patterns)
- Perplexity-style Company Research with watchlist sync
- AI Market Predictions (scrapes news, crypto, world events, foreign markets, congressional trades)
- Multi-Model AI Hypothesis Engine (GPT-5.2, Claude Sonnet 4.5, Gemini Pro, Consensus)
- AI Strategy Builder — Describe strategies in plain English, GPT-5.2 generates structured logic
- Strategy Backtester — Simulates strategies against historical Alpha Vantage data
- Strategy Marketplace — Publish, browse, search, clone community strategies (Pro-only publish/clone)
- **AI Intelligence Hub** (NEW):
  - **AI Stock Scoring** (Danelfin-style) — 1-10 scores with technical/fundamental/sentiment breakdown, recommendation, target range, key factors
  - **Pattern Recognition** (Tickeron-style) — AI-powered chart pattern detection with confidence scores, support/resistance levels, overall bias
  - **Quick Briefs** (Prospero-style) — 30-second stock summaries with headline, verdict, key metrics, catalysts, risks, action
- Macro Intelligence Dashboard (Finnhub earnings, insider trades, congressional data)
- Stripe subscription gateway ($45/month)
- JWT Bearer token authentication with referral system
- Push notifications (VAPID), Daily email digests (Resend)
- Trading Journal with P&L analytics
- Admin Panel with Code Quality Score badge
- Docker + docker-compose configuration for self-hosting

## Recent Changes (2026-04-07, Session 4)
### Strategy Backtester Completed
- Wired BacktestResults.jsx into StrategyBuilder.jsx
- Fixed EMA NaN-propagation, timezone comparison, empty-metrics, condition evaluator
- 9/9 tests passed (iteration 27)

### Strategy Marketplace Added
- Publish/browse/search/clone community strategies
- 14/14 tests passed (iteration 28)

### AI Intelligence Hub Added
- 3 new endpoints: /api/intelligence/score, /api/intelligence/patterns, /api/intelligence/brief
- Uses Alpha Vantage price data + GPT-5.2 analysis
- Tabbed UI on dashboard with ScoreView, PatternsView, BriefView
- 8/8 tests passed (iteration 29)

## Prioritized Backlog
### P1 - Next
- Alpaca broker integration (API key entry + Read/Trade) — user wants per-user broker connections
- Deploy to risedual.ai (Health check passed, Docker ready)

### P2 - Future
- Interactive Brokers / TD Ameritrade OAuth
- Migrate JWT from localStorage to httpOnly cookies
- Alpha Vantage API tier upgrade (currently free 5/min)
- Server refactoring (server.py modular routes)

## Mocked Features
- Broker trading execution (Alpaca)

## DB Collections
- chat_sessions, strategies, marketplace_strategies, payment_transactions, users

## Key API Endpoints
- POST /api/auth/login, /api/auth/register
- POST /api/chat, GET /api/research/{symbol}
- POST /api/strategy/generate, /api/strategy/backtest, /api/strategy/save
- POST /api/marketplace/publish, GET /api/marketplace/list, POST /api/marketplace/{id}/clone
- GET /api/intelligence/score/{symbol}, /api/intelligence/patterns/{symbol}, /api/intelligence/brief/{symbol}
- POST /api/subscription/create-checkout-session
