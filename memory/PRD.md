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
- **Strategy Backtester** — Simulates AI-generated strategies against historical Alpha Vantage data. Computes technical indicators (SMA, EMA, RSI, MACD, Bollinger Bands), uses AI rule interpretation, and returns metrics (win rate, P&L, Sharpe ratio, max drawdown, buy & hold comparison, monthly breakdown, trade log).
- Macro Intelligence Dashboard (Finnhub earnings, insider trades, congressional data)
- Stripe subscription gateway ($45/month)
- JWT Bearer token authentication with referral system
- Push notifications (VAPID), Daily email digests (Resend)
- Trading Journal with P&L analytics
- Admin Panel with Code Quality Score badge (A+/95) and codebase PDF download
- Docker + docker-compose configuration for self-hosting
- Promo system with countdown banners

## Recent Changes (2026-04-07, Session 4)
### Strategy Backtester Completed
- Wired `BacktestResults.jsx` into `StrategyBuilder.jsx` (state variables, UI form, results rendering)
- Fixed EMA function to handle NaN-leading arrays (MACD signal line was all-NaN)
- Fixed timezone-naive vs aware datetime comparison in price fetcher
- Fixed empty-trades metrics dict missing fields (`buy_hold_pnl`, `winning_trades`, etc.)
- Fixed condition evaluator to only check variables used in each condition (was blocking on unrelated NaN indicators like sma_200)
- Changed Alpha Vantage output to "full" for all timeframes (compact was too few data points)
- Testing: 9/9 backend tests passed (iteration 27)

## Prioritized Backlog
### P0 - Done
- Strategy Backtester (COMPLETED 2026-04-07)

### P1 - Next
- Alpaca broker integration (API key entry + Read/Trade) — user wants per-user broker connections
- Deploy to risedual.ai (Health check passed, Docker ready)

### P2 - Future
- Interactive Brokers / TD Ameritrade OAuth
- Migrate JWT from localStorage to httpOnly cookies
- Alpha Vantage API tier upgrade (currently free 5/min)
- Server refactoring (server.py → modular routes)

## Mocked Features
- Broker trading execution (Alpaca)
