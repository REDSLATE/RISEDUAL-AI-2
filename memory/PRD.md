# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Full-stack trading platform with real market data, AI chat, Stripe subscriptions, paywalled features, referral program with leaderboard + social sharing + admin promo campaigns + email notifications.

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI + PWA
- Backend: FastAPI + MongoDB (Motor Async)
- AI: Emergent Integrations (GPT-5.2 via Universal Key)
- Auth: PyJWT + bcrypt (Bearer tokens via localStorage)
- Payments: Stripe ($45/month or $486/year)
- Email: Resend (placeholder key — add real key to activate)

### Backend Modules
| Module | Responsibility |
|--------|---------------|
| `server.py` | App setup, DB init, router registration |
| `routes/auth.py` | Auth, admin, user management |
| `routes/ai.py` | Chat, hypothesis, predictions, research, portfolio, signals |
| `routes/subscription.py` | Stripe checkout, webhooks |
| `routes/workspace.py` | Watchlist, history, notifications |
| `routes/trading.py` | Broker, orders, positions |
| `routes/market.py` | Stocks, crypto, dark pool, options |
| `routes/referral.py` | Referral codes, tracking, rewards, leaderboard |
| `routes/promo.py` | Admin promo campaigns CRUD |
| `services/email_service.py` | Resend email templates + sending |

## What's Been Implemented
- [x] Full trading dashboard + Fidelity x Coinbase theme + custom logos
- [x] Alpha Vantage market data + AI Chat (GPT-5.2) + Vision
- [x] Stripe subscriptions + Company Research + Market Predictions
- [x] Macro Intelligence Dashboard + Responsive PWA + JWT Auth
- [x] Owner Admin Panel + User Workspace + Pro AI Alerts
- [x] 7 Paywall/Pro Features
- [x] Referral Program (codes, 7-day trial, 1-month rewards, 12/12 cap)
- [x] Referral Leaderboard (public, top 10, masked names)
- [x] Social Share Buttons (6 platforms, compact + full modes)
- [x] Admin-Controlled Promo Campaigns (CRUD, banner, countdown, progress)
- [x] **Email Notifications via Resend (2026-04-04)**:
  - "Your friend just signed up!" → referrer when someone uses their link
  - "You earned a free month!" → referrer when referred user subscribes to Pro
  - "Welcome to RISEDUAL AI!" → referred user with 7-day Pro trial info
  - Graceful degradation with placeholder key (logs skip, doesn't crash)
  - Professional HTML email templates matching app branding
  - Non-blocking via asyncio.create_task

## Setup Required
- **Resend**: Add real API key to `/app/backend/.env` → `RESEND_API_KEY=re_...`
  - Sign up at https://resend.com, get key from Dashboard → API Keys
  - Update `SENDER_EMAIL` to your verified domain sender

## Prioritized Backlog
### P1 - Upcoming
- Deployment to risedual.ai
### P2 - Future
- Real broker integration (Alpaca OAuth)
- Alpha Vantage API upgrade

## Mocked Features
- Broker trading execution (Alpaca)
- Email sending (placeholder Resend API key)
