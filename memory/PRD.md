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
- AI Intelligence Hub:
  - AI Stock Scoring (Danelfin-style, 1-10 breakdown)
  - Pattern Recognition (Tickeron-style, confidence %)
  - Quick Briefs (Prospero-style, 30-second summaries)
- Watchlist Intelligence — batch AI analysis of entire watchlist, 1hr MongoDB cache
- **Daily Watchlist Digest Emails** — Enhanced Resend daily digest now includes personalized Watchlist Intelligence section: health score badge, per-ticker AI scores + verdicts, alerts (oversold/overbought/breakout), top movers. Pre-generation scheduler at 5:30 AM UTC, digest emails at 6:00 AM UTC.
- Macro Intelligence Dashboard (Finnhub earnings, insider, congressional)
- Stripe subscription ($45/month), JWT auth, referral system
- Push notifications (VAPID), trading journal, admin panel
- Docker deployment files

## Scheduled Jobs
- **5:30 AM UTC** — Pre-generate watchlist intelligence for all users (caches in MongoDB)
- **6:00 AM UTC** — Send daily digest emails with market data + personalized watchlist intel

## Recent Changes (2026-04-08, Session 4)
- Strategy Backtester completed (iteration 27)
- Strategy Marketplace added (iteration 28)
- AI Intelligence Hub added (iteration 29)
- Watchlist Intelligence added (iteration 30)
- Daily Watchlist Digest Emails enhanced (iteration 31)

## Prioritized Backlog
### P1 - Next
- Alpaca broker integration (per-user broker connections)
- Deploy to risedual.ai

### P2 - Future
- Interactive Brokers / TD Ameritrade OAuth
- Alpha Vantage API tier upgrade
- Server refactoring

## Mocked Features
- Broker trading execution (Alpaca)
