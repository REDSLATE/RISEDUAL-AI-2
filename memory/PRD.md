# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT named **RISEDUAL AI**. Requires real market data, crypto & dark pool data, broker connections, Stripe subscription, AI chat, and a macro prediction engine. Must be PWA with custom auth, paywalled AI Hypothesis, owner admin panel, user workspace, and 7 Pro-only paywall restrictions.

## Architecture
- Frontend: React + TailwindCSS + Shadcn UI + PWA
- Backend: FastAPI + MongoDB (Motor Async)
- AI: Emergent Integrations (GPT-5.2 via Universal Key)
- Auth: PyJWT + bcrypt (Bearer tokens via localStorage)
- Payments: Stripe (test keys, $45/month or $486/year)

### Backend Route Modules (refactored 2026-04-03)
| Module | Responsibility |
|--------|---------------|
| `server.py` (130 lines) | App setup, DB init, router registration |
| `routes/auth.py` | Auth, admin, user management |
| `routes/ai.py` | Chat, hypothesis, predictions, research, scraping, portfolio, signals, export |
| `routes/subscription.py` | Stripe checkout, webhooks |
| `routes/workspace.py` | Watchlist, history, notifications |
| `routes/trading.py` | Broker, orders, positions |
| `routes/market.py` | Stocks, crypto, dark pool, options |

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
- [x] Code quality fixes (secrets, hook deps, React keys)
- [x] Custom logo integration (flame/arrow + AI face)
- [x] Server refactor: server.py 961 -> 130 lines, split into 6 route modules
- [x] **7 Paywall/Pro Features (Completed 2026-04-04)**:
  - AI Chat rate limits (5/day free)
  - Watchlist caps (3 for free)
  - Text export for hypothesis reports (Pro only)
  - ProBlurWall on Dark Pool + Congress Trades tables
  - Chat history locks (24h for free users)
  - Real-time Market Signals (Pro-only modal)
  - AI Portfolio Analyzer (Pro-only modal)

## Prioritized Backlog
### P1 - Upcoming
- Deployment to risedual.ai
### P2 - Future
- Real broker integration (Alpaca OAuth)
- Alpha Vantage API upgrade

## Mocked Features
- Broker trading execution (Alpaca)
