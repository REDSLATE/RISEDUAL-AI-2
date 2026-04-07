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
- **AI Strategy Builder** — Describe strategies in plain English, GPT-5.2 generates structured logic
- **Strategy Backtester** — Simulates strategies against historical Alpha Vantage data (SMA, EMA, RSI, MACD, Bollinger Bands, AI rule interpretation, win rate, P&L, Sharpe ratio, drawdown, trade log)
- **Strategy Marketplace** — Publish backtested strategies, browse/search community strategies, clone to your account. Pro-only publish & clone. Sort by win rate, P&L, clones, newest.
- Macro Intelligence Dashboard (Finnhub earnings, insider trades, congressional data)
- Stripe subscription gateway ($45/month)
- JWT Bearer token authentication with referral system
- Push notifications (VAPID), Daily email digests (Resend)
- Trading Journal with P&L analytics
- Admin Panel with Code Quality Score badge (A+/95)
- Docker + docker-compose configuration for self-hosting
- Promo system with countdown banners

## Recent Changes (2026-04-07, Session 4)
### Strategy Backtester Completed
- Wired BacktestResults.jsx into StrategyBuilder.jsx
- Fixed EMA NaN-propagation, timezone comparison, empty-metrics fields, condition evaluator
- 9/9 backend tests passed (iteration 27)

### Strategy Marketplace Added
- Backend: POST /api/marketplace/publish, GET /api/marketplace/list, GET /api/marketplace/{id}, POST /api/marketplace/{id}/clone
- Frontend: StrategyMarketplace.jsx with search, sort (win_rate/pnl/clones/newest), expandable cards
- Publish button in StrategyBuilder after backtest results
- Accessible from Strategies dropdown and user profile menu
- 14/14 backend tests passed (iteration 28)

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
- `chat_sessions`, `strategies`, `marketplace_strategies`, `payment_transactions`, `users`
