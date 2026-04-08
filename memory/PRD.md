# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. It requires real market data integration, crypto & dark pool data, functional trading broker connections, a Stripe subscription gateway, an AI chat assistant, a highly complex AI market prediction engine that scrapes macro data, an AI Strategy Builder, Strategy Backtester, Strategy Marketplace, AI Intelligence Hub, Watchlist Intelligence, Resend email pipelines, and full authentication with password reset.

## Tech Stack
- **Frontend**: React, TailwindCSS, Shadcn UI, Recharts
- **Backend**: FastAPI, MongoDB (Motor Async), APScheduler
- **3rd Party**: OpenAI GPT-5.2, Claude Sonnet 4.5, Gemini (Emergent LLM Key), Alpha Vantage, Finnhub, Stripe, Resend
- **Architecture**: Strict non-blocking async (`asyncio.to_thread` for external calls)

## UI Theme
- Deep navy background `#0F172A`, blue accents `#0052FF`, glassmorphism, rounded-xl cards
- "Fidelity x Coinbase" aesthetic — DO NOT CHANGE

## Core Features (All Implemented)
1. Real-time Stock/Crypto Tickers (Alpha Vantage + CoinGecko)
2. Options Radar & Flow Screener
3. Dark Pool Data Tables
4. Multimodal AI Chat (image upload, chart patterns)
5. Perplexity-style Company Research
6. Macro Intelligence Dashboard (World Events, Foreign Markets, Gov Filings)
7. AI Market Predictions (multi-source scraping)
8. Stripe Subscription Gateway ($45/month)
9. AI Strategy Builder & Backtester
10. Strategy Marketplace (publish/clone)
11. AI Intelligence Hub (Scoring, Patterns, Briefs)
12. Watchlist Intelligence Dashboard
13. Daily Digest Emails (Resend)
14. Referral System with email notifications
15. Full Auth (Register, Login, Brute Force Protection, Forgot/Reset Password)
16. Admin Panel (Owner-only user management)
17. PWA Support (Service Worker)

## Authentication System
- JWT-based (localStorage Bearer tokens due to K8s ingress CORS)
- Bcrypt password hashing
- Access tokens (15min) + Refresh tokens (7 days)
- Brute force protection (5 attempts = 15min lockout)
- **Forgot Password**: Email-based reset via Resend with 1-hour expiry tokens
- **Password Reset**: Token-based reset with validation (min 6 chars)
- Admin seeding on startup (admin + owner accounts)

## Database Collections
- `users`: email, password_hash, role, subscription_status, is_active
- `chat_sessions`: session_id, user_id, messages, created_at
- `payment_transactions`: session_id, amount, currency, plan, status
- `strategies`: user_id, name, summary, is_public, clones
- `password_reset_tokens`: token, user_id, expires_at, used (TTL index)
- `login_attempts`: identifier, attempts, locked_until

## Key API Endpoints
- `POST /api/auth/login`, `/register`, `/logout`, `/me`, `/refresh`
- `POST /api/auth/forgot-password`, `/reset-password`
- `GET /api/auth/admin/users` (owner-only)
- `GET /api/research/{symbol}`
- `POST /api/chat`
- `POST /api/subscription/create-checkout-session`
- `POST /api/strategy/publish`

## What's Remaining (Prioritized Backlog)

### P1 - Real Broker Integrations
Replace mock Alpaca/Interactive Brokers trading endpoints with real authenticated OAuth flows. Users should be able to connect their own brokerage accounts.

### P2 - Deployment
Deploy to user's GoDaddy domain (`risedual.ai`). Determine whether to use Emergent deployment or self-host.

### P3 - Alpha Vantage Upgrade
Handle API limit upgrades when user moves from free tier (5 calls/min).

### P4 - Server Refactoring
Break down `server.py` entirely into modular `routes/` directory.

## Last Updated
- **Feb 2026**: Implemented Forgot Password flow (email form, Resend integration, reset token, frontend modals)
