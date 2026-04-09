# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Requires real market data, crypto & dark pool data, functional trading broker connections, a Stripe subscription gateway ($45/month), an AI chat assistant, and a highly complex AI market prediction engine that scrapes financial news, crypto transactions, and real estate data. Deep navy `#0F172A` background with blue `#0052FF` accents ("Fidelity × Coinbase" aesthetic).

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **Auth**: httpOnly secure cookies (JWT), 90s fetch timeout for AI endpoints
- **AI**: Emergent LLM Key (GPT-5.2, Claude Sonnet 4.5, Gemini)
- **Payments**: Stripe ($45/month Pro subscription)
- **Market Data**: Alpha Vantage (free tier), Finnhub
- **Email**: Resend

## Core Features (All Implemented)
- Real-time stock & crypto tickers
- Options Radar, Dark Pool tables
- AI War Room (unified command center)
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

## Deployment Status
- Health Check: PASSED (April 2026)
- All 21 features verified via testing agent (100% pass)
- Ready for production deployment to risedual.ai

## Known Limitations
- Broker integrations are mocked (no real OAuth flows)
- Alpha Vantage on free tier (5 calls/min)
- Finnhub Congressional Trading API returns 403 on free tier
- Reuters RSS feeds may not resolve in certain network environments

## Backlog
- P1: Deploy to risedual.ai (GoDaddy domain)
- P2: Alpha Vantage API tier upgrade
- P3: Advanced Sector Heatmap charting
- P4: Real broker OAuth flows
