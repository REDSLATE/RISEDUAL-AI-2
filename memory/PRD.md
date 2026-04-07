# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Full-stack trading platform with real market data, AI chat, Stripe subscriptions, paywalled features, referral program (leaderboard, social sharing, admin promos, email notifications), daily digest emails, and PWA push notifications.

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI + PWA (Service Worker + Push)
- Backend: FastAPI + MongoDB (Motor Async) + APScheduler + pywebpush
- AI: Emergent Integrations (GPT-5.2, Claude Sonnet 4.5, Gemini Pro via Universal Key)
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

## Code Quality Review Round 2 (Applied 2026-04-07)
All critical and important findings resolved:
- **Circular imports ELIMINATED**: Extracted `services/auth_helpers.py` with `get_current_user`, `get_optional_user`, `is_pro_user`. All 7 route modules now import from auth_helpers instead of routes.auth
- **Duplicate `is_pro_user` removed**: Consolidated from ai.py, journal.py, workspace.py into single auth_helpers source
- **Hardcoded secrets in tests**: Fixed remaining 3 occurrences (test_multi_model_hypothesis.py, conftest_creds.py)
- **Journal analytics refactored**: Split 74-line `get_analytics()` into `_calculate_win_rate()`, `_aggregate_by_ticker()`, `_build_pnl_timeline()`
- **Index-as-key fixed**: StockTicker, PortfolioAnalyzer, MobileBottomNav, MacroDashboard — all using stable keys
All critical and important findings from the code quality audit have been resolved:
- **Circular imports**: Confirmed no actual circular dependency (lazy imports already in place)
- **Dynamic import security**: Replaced `__import__('bson')` with explicit `from bson import ObjectId`
- **Hardcoded secrets in tests**: All 15 occurrences replaced with `os.environ.get()` + `conftest_creds.py`
- **Hook dependency violations**: Added eslint-disable with rationale for module-level constant deps
- **Empty catch blocks**: All 12 silent catches now log via `console.error()`
- **`is`/`==` comparisons**: Fixed `is False` → `not user.get("is_active", True)`
- **Insecure `random`**: Replaced with `secrets.SystemRandom()` in market_data_service.py
- **Index-as-key**: Fixed 7 occurrences to use stable keys (data IDs, unique text, composite keys)
- **Unused variables**: Cleaned up across 4 services (crypto, digest, push, real_estate)
- **f-string placeholders**: Auto-fixed 7 empty f-strings

## Documents Generated
- **Complete Codebase PDF** (305 pages, 0.7MB): All source code, architecture, DB schemas, API docs, setup guide, env config, and test suite
  - Download: `GET /api/download/codebase-pdf`
  - Admin Panel > Tools tab > "Download Source Code" button
  - Also available at: `/app/RISEDUAL_AI_Complete_Codebase.pdf`

## Multi-Model AI Hypothesis (Added 2026-04-07)
- **Models Available**: GPT-5.2 (OpenAI), Claude Sonnet 4.5 (Anthropic), Gemini Pro (Google)
- **Consensus Mode**: Runs all 3 models in parallel, weighted voting (35/35/30), agreement %
- **Access Control**: Free users → GPT-5.2 only (teaser). Pro users → all models + Consensus
- **API**: `GET /api/hypothesis/{symbol}?model={gpt-5.2|claude-sonnet-4.5|gemini-pro|consensus}`
- **Service**: `/app/backend/services/multi_model_hypothesis_service.py`

## Prioritized Backlog
### P1 - Upcoming
- Deployment to risedual.ai
### P2 - Future
- Real broker integration (Alpaca OAuth)
- Alpha Vantage API upgrade

## Mocked Features
- Broker trading execution (Alpaca)
- Email sending (placeholder Resend API key)
