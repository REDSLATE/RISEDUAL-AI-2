# RISEDUAL AI — Product Requirements Document

## Original Problem Statement
Build **RISEDUAL AI** — an advanced AI-powered trading intelligence platform. Requires real market data, crypto & dark pool data, functional trading broker connections, a Stripe subscription gateway ($45/month), an AI chat assistant, and a highly complex AI market prediction engine.

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (port 3000)
- **Backend**: FastAPI + MongoDB via Motor Async (port 8001)
- **Auth**: httpOnly secure cookies (JWT), 90s fetch timeout for AI endpoints
- **AI**: Emergent LLM Key (GPT-5.2, Claude Sonnet 4.5, Gemini)
- **Payments**: Stripe ($45/month Pro subscription)
- **Market Data**: Alpha Vantage with yfinance fallback, Finnhub
- **Email**: Resend

## Critical Technical Decisions
- **Motor DB Boolean Checks**: NEVER use `if db:`. ALWAYS use `if db is not None:`
- **No AbortController in authFetch**: Causes postMessage clone errors
- **90s fetch timeout**: AI endpoints take 8-50s
- **Unified Price Provider**: `price_provider.py` (AV → yfinance → MongoDB cache)

## Core Features (All Implemented)
- Real-time stock & crypto tickers, Options Radar, Dark Pool
- AI War Room, AI Intelligence Hub, AI Investment Hypothesis
- Multimodal AI Chat + Voice (TTS/STT), Persistent Chat Memory
- Company Research, Market Predictions, Sector Heatmap + AI Sentiment
- P&L Tracker, 8 Broker Integrations, Stripe Gateway
- Admin Panel (Cache, Security Audit, Success Fees, Media, Waitlist)
- Referral System, Trading Journal, Strategy Builder/Marketplace
- Market Vector Memory (ChromaDB), VAPID Push, Order Flow Heatmaps
- Whale Radar (10 crypto SSE), Paper Trading, Portfolio AI Agent
- Legal Pages, SEO, Beta Waitlist, User Badges
- Smart Orders, Risk Calculator, Market Scanner + AI Validation
- Trading Bots (Grid/Signal/Webhook), Help Center, Onboarding Tour

### Success Fee System (April 13, 2026)
- 1.5% on broker-connected users' monthly gains above $1,000
- Zero fee on losses/breakeven/below threshold
- Monthly billing resets 1st. Manual collection via Admin Panel.
- Backend: 6 endpoints under `/api/success-fee/`
- Frontend: SuccessFeeWidget (dashboard), SuccessFeesTab (admin)
- Legal: Terms of Service Section 6, Landing page pricing + FAQ
- **Verified (Iteration 116)**: 96% backend, 100% frontend

### Badge Showcase + Public Profiles (April 13, 2026)
- **Leaderboard badges**: Each entry shows badge pill (Creator, F100, Beta, Pro)
- **Clickable names**: Open PublicProfile modal
- **Public Profile modal**: Avatar, masked name, primary badge, rank, referrals, join date, "Badges Earned" section
- **Backend**: `GET /api/referral/profile/{user_id}` public endpoint, leaderboard returns `user_id` + `badge`
- **Badge priority**: Creator (owner/admin) > Founding 100 > Beta > Pro > Free
- **Verified (Iteration 117)**: 94% backend, 100% frontend

## Backlog
- P1: Deploy to `risedual.ai` custom domain
- P2: Alpha Vantage API upgrade guidance
