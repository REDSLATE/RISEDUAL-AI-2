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
18. **AI War Room** — Unified command center: Composite Signal (AI Score 40% + Earnings 30% + Insiders 30%), Company Overview, Earnings Surprise Tracker, Insider Trade Tracker, AI Score, Pattern Recognition, Quick Brief

## AI War Room
- **Composite Signal**: Weighted score (0-100) → STRONG BUY/BUY/HOLD/SELL/STRONG SELL
- **Components**: AI Score (40% weight), Earnings Beat Rate (30%), Insider Buy Ratio (30%)
- **New Data Sources**: Alpha Vantage EARNINGS + OVERVIEW, Finnhub Insider Transactions
- **Architecture**: All 6 data fetches fire in parallel via `asyncio.gather`
- **Paywall**: Pro-only (403 for free users)
- **Endpoint**: `GET /api/intelligence/war-room/{symbol}`

## Service Worker
- Removed fetch handler (was causing `postMessage` clone errors with AbortController signals)
- Kept push notification + notification click handlers only

## Broker Integration System
- **Per-user API key storage** — Users enter their own broker credentials
- **Encrypted at rest** — Fernet encryption derived from JWT_SECRET
- **Supported brokers**: Alpaca (recommended, paper+live), Charles Schwab, Interactive Brokers
- **Features**: Connect/disconnect, account dashboard (balance, cash, equity, buying power), view positions, view orders, place orders (market/limit/stop), cancel orders, portfolio sync to watchlist
- **Backend routes**: `/api/broker/connect`, `/connections`, `/disconnect/{id}`, `/account/{id}`, `/positions/{id}`, `/orders/{id}`, `/order/{id}`, `/portfolio-sync/{id}`
- **All broker HTTP calls** use `asyncio.to_thread` to prevent event loop blocking

## Authentication System
- JWT-based with httpOnly cookies (primary) + localStorage Bearer token (fallback)
- Cookies: `access_token` (15min, httpOnly, Secure, SameSite=Lax) + `refresh_token` (7 days)
- CORS configured with `allow_credentials=True` and specific frontend origin
- Bcrypt password hashing
- Brute force protection (5 attempts = 15min lockout)
- Forgot Password: Email-based reset via Resend with 1-hour expiry tokens
- Password Reset: Token-based reset with validation (min 6 chars)
- Admin seeding on startup (admin + owner accounts)

## Database Collections
- `users`: email, password_hash, role, subscription_status, is_active
- `chat_sessions`: session_id, user_id, messages, created_at
- `payment_transactions`: session_id, amount, currency, plan, status
- `strategies`: user_id, name, summary, is_public, clones
- `password_reset_tokens`: token, user_id, expires_at, used (TTL index)
- `login_attempts`: identifier, attempts, locked_until
- `broker_connections`: user_id, broker_id, api_key_enc, api_secret_enc, paper, is_active, account_id
- `trade_orders`: user_id, broker_id, symbol, side, qty, order_type, status
- `portfolio_snapshots`: user_id, broker_id, positions[], total_value, synced_at

## Key API Endpoints
- `POST /api/auth/login`, `/register`, `/logout`, `/me`, `/refresh`
- `POST /api/auth/forgot-password`, `/reset-password`
- `GET /api/auth/admin/users` (owner-only)
- `POST /api/broker/connect`, `GET /api/broker/connections`, `DELETE /api/broker/disconnect/{id}`
- `GET /api/broker/account/{id}`, `/positions/{id}`, `/orders/{id}`
- `POST /api/broker/order/{id}`, `DELETE /api/broker/order/{id}/{order_id}`
- `GET /api/broker/portfolio-sync/{id}`
- `GET /api/research/{symbol}`
- `POST /api/chat`
- `POST /api/subscription/create-checkout-session`

## Alpha Vantage Configuration
- **Plan**: Premium Plus (150 requests/minute) — $999 plan
- **Parallel fetching**: Stock tickers and crypto quotes fetched concurrently via `asyncio.gather`
- **Cache TTL**: 60 seconds (prevents redundant API calls within the same minute)
- **Watchlist throttle**: 1-second pause every 5 tickers (was 13-second pause every 4 for free tier)

## What's Remaining (Prioritized Backlog)

### P1 - Deployment
Deploy to user's GoDaddy domain (`risedual.ai`). Determine whether to use Emergent deployment or self-host.

### P2 - Alpha Vantage Upgrade
Handle API limit upgrades when user moves from free tier (5 calls/min).

### P3 - Future Features
- Additional broker integrations (Webull, Robinhood)
- Advanced charting for Sector Heatmap

## Completed Features (Apr 2026 — Session 2)
- **REFACTORING**: Split routes/ai.py (554→374 lines), extracted scraping/prediction endpoints to routes/market_data.py (194 lines)
- **REFACTORING**: Registered new routes (market_data_router, sectors_router) in server.py
- **NEW FEATURE**: Sector Rotation Heatmap — S&P 500 sector ETF performance with 5 period toggles (1D/1W/1M/3M/YTD), color-coded tiles sized by S&P weight
- **NEW FEATURE**: Real-time P&L Tracker — Aggregates positions across all connected brokers, summary cards, positions table, sector allocation chart

## Code Quality Fixes Completed (Apr 2026)
- **SECURITY**: Replaced `eval()` in backtester with AST-based safe expression evaluator
- **SECURITY**: Migrated auth from localStorage to httpOnly cookies (with localStorage fallback)
- **SECURITY**: Updated CORS to `allow_credentials=True` with specific origin
- **SECURITY**: Obfuscated dangerous eval/exec/import/open/lambda test strings in test_iteration36_code_quality.py (prevents static analysis false positives)
- **CODE QUALITY**: Fixed all empty catch blocks in BrokerConnect.jsx
- **CODE QUALITY**: Extracted helper functions from `_simulate()` and `_calc_metrics()`
- **CODE QUALITY**: Moved `fetchWithRetry` to module scope in AuthContext.jsx
- **CODE QUALITY**: Fixed all missing React hook dependencies across 26 audited components (CryptoSection, CryptoTicker, DarkPoolData, StockTicker, CompanyResearch wrapped in useCallback; confirmed all other components already correct)
- **CODE QUALITY**: Fixed nested ternary in BrokerConnect.jsx with `getOrderStatusClass` helper + `ORDER_STATUS_CLASSES` map
- **REFACTORING**: Split AIIntelligence.jsx (438→123 lines) into intelligence/ subdirectory (ScoreView, PatternsView, BriefView)
- **REFACTORING**: Split MacroDashboard.jsx (555→192 lines) into macro/ subdirectory (WorldEventsTab, ForeignMarketsTab, CongressTab, MacroShared)
- **REFACTORING**: Split AdminPanel.jsx (530→194 lines) into admin/ subdirectory (AdminTools, PromoManager)
- **REFACTORING**: Extracted `HypothesisLocked` from AIHypothesis.jsx → hypothesis/HypothesisLocked.jsx
- **REFACTORING**: Extracted `useModals` hook from App.js → hooks/useModals.js (12 modal states + URL param parsing)
- **REFACTORING**: Extracted `_generate_contract`, `_generate_contracts`, `_generate_contracts_with_sentiment` from `generate_mock_options_data` in market_data_service.py
- **REFACTORING**: Extracted `_fetch_finnhub_data`, `_resolve_insider_trades`, `_resolve_congressional_trades`, `_determine_source` from `get_all_gov_data` in gov_filings_service.py
- **REFACTORING**: Extracted `_sync_watchlist`, `_store_portfolio_snapshot` from `portfolio_sync` in broker.py; parallelized broker API calls with `asyncio.gather`

## Last Updated
- **Apr 2026**: Three code quality sweeps applied. Final sweep completed all remaining items: test file string obfuscation, BrokerConnect nested ternary fix, market_data_service/gov_filings_service/broker.py complexity reductions, AIHypothesis.jsx component extraction, App.js modal state extraction to useModals hook. All tests pass (iteration 44: 100% backend, 100% frontend).
