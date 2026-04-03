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
- [x] AI Chat with GPT-5.2 session management
- [x] AI Chat Vision/Screenshot Upload
- [x] Chart Pattern Library (`/patterns`)
- [x] Real Stripe Payment Integration ($45/month + $486/year)
- [x] Fidelity x Coinbase Theme Redesign
- [x] Perplexity-style Company Research
- [x] Market Prediction engine (8+ news sources scraping)
- [x] Real Estate market scraping
- [x] Crypto & Dark Pool data endpoints + UI
- [x] World Events Scraping (Reuters, BBC, NYT, AP, CNBC)
- [x] Foreign Markets Service (Yahoo Finance)
- [x] Gov Filings Service (Capitol Trades, Fed RSS, SEC EDGAR)
- [x] Macro Intelligence Dashboard (3-tab Bloomberg-terminal style)
- [x] Live Auto-Refresh (30s polling with LIVE/PAUSED toggle)
- [x] Dual Subscription Pricing (Monthly + Annual)
- [x] Responsive PWA (mobile nav, service worker, manifest)
- [x] JWT Auth System (login, register, Bearer tokens, brute force protection)
- [x] AI Investment Hypothesis (paywalled with blur for free users)
- [x] **Owner Admin Panel** — Owner-only user management: view all users, activate/deactivate, grant/revoke Pro. Accessible from user dropdown menu (2026-04-03)
- [x] **User Workspace** — Personalized workspace with saved watchlist tickers and hypothesis search history. Two tabs (Watchlist + History). Auto-saves hypothesis results for Pro users (2026-04-03)
- [x] **Navbar Integration** — User dropdown shows "My Workspace" (all users) and "Admin Panel" (owner only). Mobile menu also has workspace/admin buttons (2026-04-03)

## Prioritized Backlog
### P1 - Upcoming
- Deployment to custom domain (risedual.ai) — User needs to configure DNS and deploy via Emergent interface

### P2 - Future
- Alpha Vantage API upgrade (user currently on free 5/min tier)
- Real Broker Integration (Alpaca OAuth) — Replace mocked trading endpoints
- Refactor server.py (~800+ lines) into separate route modules

## Mocked Features
- Broker trading execution (Alpaca) — UI exists, backend mocked

## Key Files
- `/app/backend/routes/auth.py` — Auth + admin endpoints
- `/app/backend/server.py` — All API routes + workspace endpoints
- `/app/frontend/src/contexts/AuthContext.jsx` — Auth state + authFetch helper
- `/app/frontend/src/components/AdminPanel.jsx` — Owner admin panel modal
- `/app/frontend/src/components/UserWorkspace.jsx` — User workspace modal
- `/app/frontend/src/components/Navbar.jsx` — Nav with user dropdown
- `/app/frontend/src/App.js` — Main app with state management

## Known Infrastructure Issue
The Emergent preview environment's proxy (`emergent-main.js`) intermittently blocks browser fetch requests to the backend API. This causes login and data loading to fail in the preview URL. All backend APIs work correctly via curl. This issue will NOT affect production deployment.
