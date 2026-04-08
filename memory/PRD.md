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
- AI Strategy Builder — plain English to structured trading logic
- Strategy Backtester — historical simulation with technical indicators
- Strategy Marketplace — publish, browse, search, clone community strategies
- AI Intelligence Hub (AI Stock Scoring, Pattern Recognition, Quick Briefs)
- Watchlist Intelligence — batch AI analysis of entire watchlist
- Daily Watchlist Digest Emails — enhanced Resend digest with personalized watchlist section
- Macro Intelligence Dashboard (Finnhub earnings, insider, congressional)
- Stripe subscription ($45/month), JWT auth, referral system
- Push notifications (VAPID), trading journal, admin panel
- Docker deployment files

## Critical Bug Fix (2026-04-08)
### Async Event Loop Blocking Fix
**Problem**: All scraping services (financial_scraping, world_events, foreign_markets, gov_filings, real_estate, market_data, company_research, ai_intelligence, backtester, watchlist_intelligence) used synchronous `requests.get()` inside `async def` methods, blocking the FastAPI event loop for 30+ seconds. This caused ALL concurrent requests (including login) to fail in production.

**Fix**: Wrapped all blocking HTTP calls in `asyncio.to_thread()` across 9 service files. Added async `_get()` helper methods where appropriate.

**Result**: Login now completes in <1s even during 30s market prediction (previously 30+ seconds). 11/11 tests passed (iteration 32).

## Recent Changes (2026-04-08, Session 4)
- Strategy Backtester (iteration 27)
- Strategy Marketplace (iteration 28)
- AI Intelligence Hub (iteration 29)
- Watchlist Intelligence (iteration 30)
- Daily Watchlist Digest Emails (iteration 31)
- Async Event Loop Blocking Fix (iteration 32)

## Prioritized Backlog
### P1 - Next
- Alpaca broker integration (per-user broker connections)
- Deploy to risedual.ai (ready for redeployment with async fix)

### P2 - Future
- Interactive Brokers / TD Ameritrade OAuth
- Alpha Vantage API tier upgrade
- Server refactoring

## Mocked Features
- Broker trading execution (Alpaca)
