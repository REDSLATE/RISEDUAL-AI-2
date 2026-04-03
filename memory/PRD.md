# RISEDUALAI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT (TradeAlgo) named **RISEDUALAI**. It should have an independent look but the same trading processes. Requires real market data integration, crypto & dark pool data, functional trading broker connections, a subscription gateway, an AI chat assistant, and a highly complex AI market prediction engine that scrapes financial news, crypto transactions, and real estate data. The app must be fully responsive, function as a Progressive Web App (PWA), and include a custom login workspace with a paywalled AI Hypothesis feature.

## Core Requirements
- Real-time stock/crypto market data (Alpha Vantage)
- Options radar, dark pool, and crypto data
- AI Chat Assistant (GPT-5.2) with vision/image analysis
- Market Prediction engine with web scraping (8+ sources)
- Real estate market scraping
- Subscription pricing ($45/month, $486/year) via Stripe
- Broker connection (Alpaca)
- "Fidelity x Coinbase" theme (#0F172A navy background, #0052FF blue accents)
- PWA with mobile responsiveness
- JWT Bearer token authentication
- AI Investment Hypothesis with paywall (free=blurred, pro=full access)
- Owner Admin Panel with user management
- User Workspace with saved watchlist and hypothesis history
- Pro-only AI Alerts (verdict change notifications)

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI + PWA
- Backend: FastAPI + MongoDB (Motor Async)
- AI: Emergent Integrations (GPT-5.2 via Universal Key)
- Scraping: BeautifulSoup4 + lxml
- Auth: PyJWT + bcrypt (Bearer tokens via localStorage)
- Payments: Stripe (test keys)

## What's Been Implemented
- [x] Full trading dashboard UI (Navbar, StockTicker, OptionsRadar, CryptoTicker, DarkPool)
- [x] Alpha Vantage real market data integration
- [x] AI Chat with GPT-5.2 session management + Vision/Screenshot Upload
- [x] Chart Pattern Library (`/patterns`)
- [x] Real Stripe Payment Integration ($45/month + $486/year)
- [x] Fidelity x Coinbase Theme Redesign
- [x] Perplexity-style Company Research
- [x] Market Prediction engine (8+ news sources scraping)
- [x] Real Estate market scraping
- [x] Crypto & Dark Pool data endpoints + UI
- [x] World Events / Foreign Markets / Gov Filings Scraping
- [x] Macro Intelligence Dashboard (Bloomberg-terminal style)
- [x] Live Auto-Refresh (30s polling)
- [x] Responsive PWA (mobile nav, service worker, manifest)
- [x] JWT Auth System (login, register, Bearer tokens, brute force protection)
- [x] AI Investment Hypothesis (paywalled with blur for free users)
- [x] Owner Admin Panel — user management (activate/deactivate, grant/revoke Pro) (2026-04-03)
- [x] User Workspace — saved watchlist + hypothesis history (2026-04-03)
- [x] **Pro-only AI Alerts** — Real notification system: detects verdict changes (BUY→SELL etc.) when a Pro user re-analyzes a ticker. Shows watchlist star icon, confidence %, timestamps. Free users see a paywall with "Upgrade to Pro" CTA. Bell icon with unread count badge. Auto-polls every 60s. (2026-04-03)

## Prioritized Backlog
### P1 - Upcoming
- Deployment to custom domain (risedual.ai)

### P2 - Future
- Alpha Vantage API upgrade (free tier → premium)
- Real Broker Integration (Alpaca OAuth)
- Refactor server.py (~900+ lines) into separate route modules

## Mocked Features
- Broker trading execution (Alpaca) — UI exists, backend mocked

## Key Files
- `/app/backend/routes/auth.py` — Auth + admin endpoints
- `/app/backend/server.py` — All API routes + workspace + notification endpoints
- `/app/frontend/src/contexts/AuthContext.jsx` — Auth state + authFetch helper
- `/app/frontend/src/components/AdminPanel.jsx` — Owner admin panel
- `/app/frontend/src/components/UserWorkspace.jsx` — User workspace
- `/app/frontend/src/components/AlertsPanel.jsx` — Pro-only notification bell + paywall
- `/app/frontend/src/components/Navbar.jsx` — Nav with user dropdown
- `/app/frontend/src/App.js` — Main app with state management

## Key API Endpoints
- `POST /api/auth/login` — JWT login
- `GET /api/auth/me` — Current user
- `GET /api/auth/admin/users` — Owner-only user list
- `GET /api/hypothesis/{symbol}` — AI hypothesis (paywalled)
- `GET /api/workspace/watchlist` — User's saved tickers
- `GET /api/workspace/history` — Hypothesis history
- `GET /api/notifications` — Pro-only alerts
- `POST /api/notifications/read-all` — Mark all read
- `POST /api/subscription/create-checkout-session` — Stripe checkout

## Known Infrastructure Issue
The Emergent preview environment's proxy intermittently blocks browser fetch requests. All backend APIs work correctly via curl. This does NOT affect production deployment.
