# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Full-stack trading platform with real market data, AI chat, Stripe subscriptions, paywalled features, referral program (leaderboard, social sharing, admin promos, email notifications), daily digest emails, and PWA push notifications.

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI + PWA (Service Worker + Push)
- Backend: FastAPI + MongoDB (Motor Async) + APScheduler + pywebpush
- AI: Emergent Integrations (GPT-5.2 via Universal Key)
- Auth: PyJWT + bcrypt (Bearer tokens via localStorage)
- Payments: Stripe ($45/month or $486/year)
- Email: Resend (placeholder key)
- Push: Web Push with VAPID keys (configured)

### Backend Modules
| Module | Responsibility |
|--------|---------------|
| `server.py` | App setup, DB, routers, APScheduler |
| `routes/auth.py` | Auth, admin, user management |
| `routes/ai.py` | Chat, hypothesis, predictions, research, portfolio, signals |
| `routes/subscription.py` | Stripe checkout, webhooks |
| `routes/workspace.py` | Watchlist, history, notifications |
| `routes/trading.py` | Broker, orders, positions |
| `routes/market.py` | Stocks, crypto, dark pool, options |
| `routes/referral.py` | Referral codes, tracking, rewards, leaderboard |
| `routes/promo.py` | Admin promo campaigns CRUD |
| `routes/digest.py` | Daily digest (trigger, opt-in/out, preview) |
| `routes/push.py` | Push subscribe/unsubscribe/status/test/broadcast |
| `services/email_service.py` | Resend email templates |
| `services/digest_service.py` | Digest data + HTML |
| `services/push_service.py` | Web Push + notification triggers |

## What's Been Implemented
- [x] Full trading dashboard + Fidelity x Coinbase theme + custom logos
- [x] Alpha Vantage market data + AI Chat (GPT-5.2) + Vision
- [x] Stripe subscriptions + Company Research + Market Predictions
- [x] Macro Intelligence Dashboard + Responsive PWA + JWT Auth
- [x] Owner Admin Panel + User Workspace + Pro AI Alerts
- [x] 7 Paywall/Pro Features
- [x] Referral Program + Leaderboard + Social Share + Admin Promos
- [x] Email Notifications (Resend, 3 referral event types)
- [x] Daily Digest Emails (6 AM UTC, Pro full / free teaser)
- [x] **PWA Push Notifications (2026-04-04)**:
  - VAPID keys generated and configured
  - 5 trigger types: prediction flips, dark pool spikes, watchlist alerts, market signals, admin broadcast
  - Pro users: unlimited notifications
  - Free users: 1/day teaser (rate limited via push_log collection)
  - Enable/disable toggle in UserWorkspace (PushToggle component)
  - Service worker handles push events and notification clicks
  - Admin: test push and broadcast endpoints

## Setup Required
- **Resend**: Add real API key → `RESEND_API_KEY=re_...` in backend .env

## Documents Generated
- **Complete Codebase PDF** (305 pages, 0.7MB): All source code, architecture, DB schemas, API docs, setup guide, env config, and test suite
  - Download: `GET /api/download/codebase-pdf`
  - Admin Panel > Tools tab > "Download Source Code" button
  - Also available at: `/app/RISEDUAL_AI_Complete_Codebase.pdf`

## Prioritized Backlog
### P1 - Upcoming
- Deployment to risedual.ai
### P2 - Future
- Real broker integration (Alpaca OAuth)
- Alpha Vantage API upgrade

## Mocked Features
- Broker trading execution (Alpaca)
- Email sending (placeholder Resend API key)
