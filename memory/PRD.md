# RISEDUAL AI - Product Requirements Document

## Original Problem Statement
Build a functional clone of TradealgoGPT (TradeAlgo) named RISEDUAL AI with real market data, crypto & dark pool data, functional trading broker connections, Stripe subscriptions, an AI chat assistant, and a highly complex AI market prediction engine.

## Core Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (PWA, "Fidelity x Coinbase" aesthetic)
- **Backend**: FastAPI + MongoDB (Motor Async) + APScheduler
- **Integrations**: Emergent LLM (GPT-5.2, Claude 4.5, Gemini), Stripe, Finnhub, Alpha Vantage, Resend

## What's Been Implemented
- Real-time stock/crypto tickers with Alpha Vantage
- Options Radar, Dark Pool tables
- Multimodal AI Chat (image upload + chart patterns)
- Perplexity-style Company Research with watchlist sync
- AI Market Predictions (scrapes news, crypto, world events, foreign markets, congressional trades)
- Multi-Model AI Hypothesis Engine (GPT-5.2, Claude Sonnet 4.5, Gemini Pro, Consensus)
- Macro Intelligence Dashboard (Finnhub earnings, insider trades, congressional data)
- Stripe subscription gateway ($45/month)
- JWT Bearer token authentication with referral system
- Push notifications (VAPID)
- Daily email digests (Resend, verified domain support@risedual.ai)
- Trading Journal with P&L analytics
- Admin Panel with codebase PDF download
- Promo system with countdown banners

## Code Quality Fixes Applied (2026-04-07)
### Critical
1. Hardcoded test secrets replaced with `os.environ.get()` fallbacks
2. React Hook dependency violations fixed in `usePushNotifications.js`, `AuthContext.jsx`, `UserWorkspace.jsx`, `TradingJournal.jsx`

### Complexity Refactoring
3. `routes/ai.py`: Extracted `_collect_all_scrape_data()`, `_build_hypothesis_teaser()`, `_track_verdict_change()`, `_run_prediction_model()`, `_enrich_prediction_metadata()`
4. `gov_filings_service.py`: Extracted `_scrape_capitol_trades()`, `_parse_capitol_row()`, `_parse_politician()`, `_parse_issuer()`
5. `digest_service.py`: Extracted `_build_section_rows()`, `_prediction_row()`, `_dark_pool_row()`, `_signal_row()`, `_upgrade_cta_html()`

### Component Splitting
6. `AIHypothesis.jsx` (466→188 lines) → `hypothesis/ModelSelector.jsx` + `hypothesis/HypothesisResults.jsx`
7. `MarketPrediction.jsx` (412→170 lines) → `prediction/PredictionCards.jsx`
8. `TradeGPTChat.jsx` (357→173 lines) → `chat/ChatComponents.jsx`

### Performance
9. `TradingJournal.jsx`: useMemo for sorted entries, extracted chart style constants
10. `AuthContext.jsx`: useMemo for context value, useCallback for login/register/logout

### Readability
11. `DataTable.jsx`: Replaced nested ternaries with `getCellContent()` function
12. `MacroDashboard.jsx`: Extracted `getMarketStateLabel()`, `getPartyStyle()`, `getTradeTypeColor()` helpers
13. `UserWorkspace.jsx`: Extracted `TabContent`, `WatchlistTab`, `HistoryTab`, `PushToggleLabel` components

## Bug Fixes Applied (2026-04-07)
- Finnhub Congressional Trading 403: Per-category fallback (Finnhub for insider/earnings, scraping for congressional)
- MongoDB Query Projections: Added `{_id: 0}` to all find/find_one calls in ai.py, workspace.py, journal.py

## Prioritized Backlog
### P0 - Ready
- Deploy to risedual.ai (Health check passed, zero blockers)

### P2 - Future
- Real broker OAuth integration (Alpaca/Interactive Brokers)
- Migrate JWT from localStorage to httpOnly cookies (security hardening)

## Mocked Features
- Broker trading execution (Alpaca)
