# RISEDUALAI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT (TradeAlgo) named **RISEDUALAI**. Requires real market data, crypto & dark pool data, broker connections, Stripe subscription, AI chat, and a macro prediction engine. Must be PWA with custom auth, paywalled AI Hypothesis, owner admin panel, and user workspace.

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI + PWA
- Backend: FastAPI + MongoDB (Motor Async)
- AI: Emergent Integrations (GPT-5.2 via Universal Key)
- Auth: PyJWT + bcrypt (Bearer tokens via localStorage)
- Payments: Stripe (test keys)

## What's Been Implemented
- [x] Full trading dashboard UI + Fidelity x Coinbase theme
- [x] Alpha Vantage real market data
- [x] AI Chat (GPT-5.2) + Vision + Chart Patterns
- [x] Stripe ($45/month + $486/year)
- [x] Perplexity-style Company Research
- [x] Market Prediction (8+ scraping sources)
- [x] World Events / Foreign Markets / Gov Filings
- [x] Macro Intelligence Dashboard
- [x] Responsive PWA
- [x] JWT Auth (login, register, brute force)
- [x] AI Investment Hypothesis (paywall)
- [x] Owner Admin Panel (2026-04-03)
- [x] User Workspace (2026-04-03)
- [x] Pro-only AI Alerts (2026-04-03)
- [x] **Code Quality Fixes** (2026-04-03):
  - Removed hardcoded secrets (OWNER_EMAIL/PASSWORD → .env)
  - Test files use os.environ for credentials
  - Fixed unused variables in auth.py
  - Added security architecture comment for localStorage tokens
  - Fixed empty catch block in AuthContext
  - Added eslint-disable comments for intentional hook dependency omissions
  - Replaced 27+ array-index React keys with stable identifiers across 5 components
  - Added logging import for auth seed guard

## Prioritized Backlog
### P1 - Upcoming
- Deployment to risedual.ai
### P2 - Future  
- Refactor server.py into route modules
- Alpha Vantage API upgrade
- Real broker integration (Alpaca OAuth)

## Mocked Features
- Broker trading execution (Alpaca)

## Key Files
- `/app/backend/routes/auth.py` — Auth + admin endpoints
- `/app/backend/server.py` — All API routes
- `/app/frontend/src/contexts/AuthContext.jsx` — Auth state
- `/app/frontend/src/components/AdminPanel.jsx` — Owner admin
- `/app/frontend/src/components/UserWorkspace.jsx` — Workspace
- `/app/frontend/src/components/AlertsPanel.jsx` — Pro-only notifications
- `/app/frontend/src/components/MacroDashboard.jsx` — Macro data
- `/app/frontend/src/components/MarketPrediction.jsx` — AI predictions
- `/app/frontend/src/components/AIHypothesis.jsx` — Paywalled hypothesis
- `/app/frontend/src/components/TradeGPTChat.jsx` — AI chat

## Known Infrastructure Issue
Emergent preview proxy intermittently blocks browser fetch requests. All backend APIs work via curl. Does NOT affect production.
