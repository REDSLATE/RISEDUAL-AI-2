# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Requires real market data, crypto & dark pool data, broker connections, Stripe subscription, AI chat, and a macro prediction engine. Must be PWA with custom auth, paywalled AI Hypothesis, owner admin panel, user workspace, 7 Pro-only paywall restrictions, and a referral program with public leaderboard.

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
- [x] Owner Admin Panel
- [x] User Workspace
- [x] Pro-only AI Alerts
- [x] Server refactor: server.py split into 7 route modules
- [x] 7 Paywall/Pro Features (2026-04-04):
  - AI Chat rate limits (5/day free)
  - Watchlist caps (3 for free)
  - Text export for hypothesis reports (Pro only)
  - ProBlurWall on Dark Pool + Congress Trades tables
  - Chat history locks (24h for free users)
  - Real-time Market Signals (Pro-only modal)
  - AI Portfolio Analyzer (Pro-only modal)
- [x] **Referral Program (2026-04-04)**:
  - Unique referral codes per user
  - 7-day free Pro trial for referred users
  - 1 free month reward for referrers when referred user subscribes
  - 12 rewards max per 12-month rolling window
  - Self-referral & duplicate prevention
  - Referral tab in User Workspace with copy link, stats, history
  - URL param detection (?ref=CODE) in AuthModal
- [x] **Referral Leaderboard (2026-04-04)**:
  - Public endpoint (no auth required)
  - Top 10 referrers with masked names (J*** D***)
  - Medal icons for top 3 (gold, silver, bronze)
  - Total participants count
  - Shown on main page + UserWorkspace Referrals tab

## Prioritized Backlog
### P1 - Upcoming
- Deployment to risedual.ai
### P2 - Future
- Real broker integration (Alpaca OAuth)
- Alpha Vantage API upgrade

## Mocked Features
- Broker trading execution (Alpaca)

## DB Collections
- `users`: email, password_hash, role, subscription_status, trial_ends_at, referred_by
- `referral_codes`: user_id, code, created_at
- `referrals`: referrer_id, referred_id, referred_email, status, reward_granted, created_at, completed_at
- `referral_rewards`: user_id, type, from_referral, referred_email, granted_at, redeemed
- `chat_sessions`, `chat_usage`, `watchlists`, `hypothesis_history`, `notifications`, `market_signals`, `payment_transactions`
