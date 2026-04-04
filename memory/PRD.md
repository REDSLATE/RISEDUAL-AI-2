# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Requires real market data, crypto & dark pool data, broker connections, Stripe subscription, AI chat, and a macro prediction engine. Must be PWA with custom auth, paywalled AI Hypothesis, owner admin panel, user workspace, 7 Pro-only paywall restrictions, referral program with leaderboard, social sharing, and admin-controlled promo campaigns.

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI + PWA
- Backend: FastAPI + MongoDB (Motor Async)
- AI: Emergent Integrations (GPT-5.2 via Universal Key)
- Auth: PyJWT + bcrypt (Bearer tokens via localStorage)
- Payments: Stripe (test keys, $45/month or $486/year)

### Backend Route Modules
| Module | Responsibility |
|--------|---------------|
| `server.py` (~137 lines) | App setup, DB init, router registration |
| `routes/auth.py` | Auth, admin, user management |
| `routes/ai.py` | Chat, hypothesis, predictions, research, scraping, portfolio, signals, export |
| `routes/subscription.py` | Stripe checkout, webhooks, referral reward trigger |
| `routes/workspace.py` | Watchlist, history, notifications |
| `routes/trading.py` | Broker, orders, positions |
| `routes/market.py` | Stocks, crypto, dark pool, options |
| `routes/referral.py` | Referral codes, tracking, rewards, leaderboard |
| `routes/promo.py` | Promo campaigns CRUD, active promo, user progress |

## What's Been Implemented
- [x] Full trading dashboard UI + Fidelity x Coinbase theme + custom logos
- [x] Alpha Vantage real market data + AI Chat (GPT-5.2) + Vision
- [x] Stripe ($45/month + $486/year) + Perplexity-style Company Research
- [x] Market Prediction (8+ scraping sources) + Macro Intelligence Dashboard
- [x] Responsive PWA + JWT Auth + Owner Admin Panel + User Workspace
- [x] 7 Paywall/Pro Features (chat limits, watchlist cap, export, blur walls, history lock, signals, portfolio)
- [x] Referral Program (codes, 7-day trial, 1-month rewards, 12/12 cap)
- [x] Referral Leaderboard (public, top 10, masked names, medals)
- [x] Social Share Buttons (Twitter/X, LinkedIn, Facebook, WhatsApp, Telegram, Email)
- [x] **Admin-Controlled Promo Campaigns (2026-04-04)**:
  - Admin Panel > Promos tab for creating/managing campaigns
  - Set title, message, referral target, reward months, start/end dates
  - Toggle active/pause, delete campaigns
  - Sticky dismissable PromoBanner at top of page with countdown timer
  - Progress bar for logged-in users tracking referrals toward promo goal
  - LIVE/PAUSED/EXPIRED badges in admin view
  - Default promo: "Launch Week Special — Refer 3 friends, get 2 bonus months free"

## Prioritized Backlog
### P1 - Upcoming
- Deployment to risedual.ai
### P2 - Future
- Real broker integration (Alpaca OAuth)
- Alpha Vantage API upgrade

## Mocked Features
- Broker trading execution (Alpaca)
