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
- **AI Strategy Builder** — Users describe strategies in plain English, GPT-5.2 generates structured logic with indicators, entry/exit rules, risk management. Save/load/delete functionality.
- Macro Intelligence Dashboard (Finnhub earnings, insider trades, congressional data)
- Stripe subscription gateway ($45/month)
- JWT Bearer token authentication with referral system
- Push notifications (VAPID), Daily email digests (Resend)
- Trading Journal with P&L analytics
- Admin Panel with **Code Quality Score badge** (A+/95) and codebase PDF download
- Docker + docker-compose configuration for self-hosting
- Promo system with countdown banners

## Recent Changes (2026-04-07, Session 3)
### New Features
- **AI Strategy Builder**: `POST /api/strategy/generate`, `POST /api/strategy/save`, `GET /api/strategy/list`, `DELETE /api/strategy/{name}` — Full CRUD with GPT-5.2 via Emergent LLM
- **Code Quality Score Badge**: `GET /api/admin/code-quality` — Shows score, grade (A+), breakdown, and metrics in Admin Panel
- **Docker Support**: `Dockerfile` (multi-stage), `docker-compose.yml` (MongoDB + Backend + Frontend/Nginx), `nginx.conf`, `.dockerignore`

### Code Quality Fixes Applied
1. Hardcoded test secrets → env vars
2. React Hook dependency violations fixed (4 files)
3. Python complexity refactoring (ai.py, gov_filings_service.py, digest_service.py)
4. useMemo/useCallback performance optimizations
5. Nested ternaries → helper functions
6. Component splitting: AIHypothesis (466→188), MarketPrediction (412→170), TradeGPTChat (357→173)

## Prioritized Backlog
### P0 - Ready
- Deploy to risedual.ai (Health check passed, Docker ready)

### P1 - Next
- Alpaca broker integration (API key entry + Read/Trade)

### P2 - Future
- Interactive Brokers / TD Ameritrade OAuth
- Migrate JWT from localStorage to httpOnly cookies

## Mocked Features
- Broker trading execution (Alpaca)
