# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Full-stack trading platform with real market data, AI chat, Stripe subscriptions, paywalled features, referral program (leaderboard, social sharing, admin promos, email notifications), and daily digest emails.

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI + PWA
- Backend: FastAPI + MongoDB (Motor Async) + APScheduler
- AI: Emergent Integrations (GPT-5.2 via Universal Key)
- Auth: PyJWT + bcrypt (Bearer tokens via localStorage)
- Payments: Stripe ($45/month or $486/year)
- Email: Resend (placeholder key — add real key to activate)

### Backend Modules
| Module | Responsibility |
|--------|---------------|
| `server.py` | App setup, DB init, router registration, APScheduler |
| `routes/auth.py` | Auth, admin, user management |
| `routes/ai.py` | Chat, hypothesis, predictions, research, portfolio, signals |
| `routes/subscription.py` | Stripe checkout, webhooks |
| `routes/workspace.py` | Watchlist, history, notifications |
| `routes/trading.py` | Broker, orders, positions |
| `routes/market.py` | Stocks, crypto, dark pool, options |
| `routes/referral.py` | Referral codes, tracking, rewards, leaderboard |
| `routes/promo.py` | Admin promo campaigns CRUD |
| `routes/digest.py` | Daily digest endpoints (trigger, opt-in/out, preview) |
| `services/email_service.py` | Resend email templates + sending |
| `services/digest_service.py` | Digest data collection + HTML generation |

## What's Been Implemented
- [x] Full trading dashboard + Fidelity x Coinbase theme + custom logos
- [x] Alpha Vantage market data + AI Chat (GPT-5.2) + Vision
- [x] Stripe subscriptions + Company Research + Market Predictions
- [x] Macro Intelligence Dashboard + Responsive PWA + JWT Auth
- [x] Owner Admin Panel + User Workspace + Pro AI Alerts
- [x] 7 Paywall/Pro Features
- [x] Referral Program + Leaderboard + Social Share + Admin Promos
- [x] Email Notifications (Resend, 3 referral event types)
- [x] **Daily Digest Emails (2026-04-06)**:
  - APScheduler runs at 6:00 AM UTC daily
  - Pro users: full predictions, dark pool moves, signals
  - Free users: teaser (1 item visible, rest blurred + upgrade CTA)
  - User opt-in/opt-out via DigestToggle in UserWorkspace
  - Admin: manual trigger + HTML preview endpoints
  - Graceful degradation with placeholder Resend key

## Setup Required
- **Resend**: Add real API key to `/app/backend/.env` → `RESEND_API_KEY=re_...`

## Prioritized Backlog
### P1 - Upcoming
- Deployment to risedual.ai
### P2 - Future
- Real broker integration (Alpaca OAuth)
- Alpha Vantage API upgrade

## Mocked Features
- Broker trading execution (Alpaca)
- Email sending (placeholder Resend API key)
