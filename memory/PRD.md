# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Requires real market data, crypto & dark pool data, broker connections, Stripe subscription, AI chat, and a macro prediction engine. Must be PWA with custom auth, paywalled AI Hypothesis, owner admin panel, user workspace, 7 Pro-only paywall restrictions, referral program with leaderboard and social sharing.

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI + PWA
- Backend: FastAPI + MongoDB (Motor Async)
- AI: Emergent Integrations (GPT-5.2 via Universal Key)
- Auth: PyJWT + bcrypt (Bearer tokens via localStorage)
- Payments: Stripe (test keys, $45/month or $486/year)

### Backend Route Modules
| Module | Responsibility |
|--------|---------------|
| `server.py` (~135 lines) | App setup, DB init, router registration |
| `routes/auth.py` | Auth, admin, user management |
| `routes/ai.py` | Chat, hypothesis, predictions, research, scraping, portfolio, signals, export |
| `routes/subscription.py` | Stripe checkout, webhooks, referral reward trigger |
| `routes/workspace.py` | Watchlist, history, notifications |
| `routes/trading.py` | Broker, orders, positions |
| `routes/market.py` | Stocks, crypto, dark pool, options |
| `routes/referral.py` | Referral codes, tracking, rewards, leaderboard |

## What's Been Implemented
- [x] Full trading dashboard UI + Fidelity x Coinbase theme + custom logos
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
- [x] Owner Admin Panel + User Workspace
- [x] Pro-only AI Alerts
- [x] Server refactor into 7 route modules
- [x] 7 Paywall/Pro Features (chat limits, watchlist cap, export, blur walls, history lock, signals, portfolio)
- [x] **Referral Program**: codes, 7-day trial, 1-month rewards, 12/12 cap, self-referral prevention
- [x] **Referral Leaderboard**: public, top 10, masked names, medal icons
- [x] **Social Share Buttons (2026-04-04)**: Twitter/X, LinkedIn, Facebook, WhatsApp, Telegram, Email
  - Full mode in UserWorkspace Referrals tab
  - Compact mode in main page Leaderboard (logged-in users)
  - Pre-written share message about RISEDUAL AI

## Prioritized Backlog
### P1 - Upcoming
- Deployment to risedual.ai
### P2 - Future
- Real broker integration (Alpaca OAuth)
- Alpha Vantage API upgrade

## Mocked Features
- Broker trading execution (Alpaca)
