# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Requires real market data, crypto & dark pool data, functional trading broker connections, a Stripe subscription gateway ($45/month), an AI chat assistant, and a highly complex AI market prediction engine that scrapes financial news, crypto transactions, and real estate data. Deep navy `#0F172A` background with blue `#0052FF` accents ("Fidelity × Coinbase" aesthetic).

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **Auth**: httpOnly secure cookies (JWT), 90s fetch timeout for AI endpoints
- **AI**: Emergent LLM Key (GPT-5.2, Claude Sonnet 4.5, Gemini)
- **Payments**: Stripe ($45/month Pro subscription)
- **Market Data**: Alpha Vantage (paid tier), Finnhub
- **Email**: Resend

## Critical Technical Decisions
- **No AbortController in authFetch**: Causes postMessage clone errors with Service Workers
- **90s fetch timeout**: AI endpoints take 8-50s; do NOT lower below 90s
- **Dynamic API base URL**: `getApiBase()` in `/app/frontend/src/utils/apiBase.js` resolves to correct domain on any deployment
- **Auto-refresh JWT**: `authFetch` silently refreshes expired access tokens on 401

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

## Deployment Status
- Health Check: PASSED (April 2026)
- All features verified via testing agent (100% pass, iteration 47)
- "Analysis failed" bug fixed (dynamic API base + auto-refresh JWT)
- Ready for production deployment to risedual.ai

## Known Limitations
- Broker integrations are mocked (no real OAuth flows)
- Finnhub Congressional Trading API returns 403 on free tier (gracefully handled)
- Reuters RSS feeds may not resolve in certain network environments (gracefully handled)

## Backlog
- P0: Re-deploy to risedual.ai with fix for "Analysis failed"
- P1: Connect custom domain risedual.ai via DNS
- P2: Alpha Vantage API tier upgrade handling
- P3: Advanced Sector Heatmap charting
- P4: Real broker OAuth flows
