# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI
- **Backend**: FastAPI + MongoDB + APScheduler
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **Payments**: Stripe Live Mode
- **Market Data**: Alpha Vantage -> Finnhub -> TwelveData (ProviderRouter)
- **Search**: War Room (DDG + Tavily + Wikipedia + SEC + FRED + Yahoo + AI Analysis + StockFit)
- **Domain**: risedual.ai

## Completed This Session
- **Financial Tools Agent refactored**: Split into `financial_tools.py` (schemas/tools) + `financial_tools_agent.py` (state machine)
- **StockFit dispatch**: All 7 SEC data types routing correctly (financials, insiders, insider_summary, earnings, scores, earnings_calendar, fund_holders)
- **Code Quality Review v1 & v2 applied**: Undefined vars, hardcoded test creds centralized to conftest_creds.py, boolean comparisons, f-string fixes, unused variables removed
- **KeyVault enhanced**: Added `POST /api/vault/keys/bulk` (bulk import), `POST /api/vault/keys/validate` (13 provider validators), frontend Validate button + help links
- **Owner 402 bug fixed**: Owner had no credit wallet → seeded 50K credits, `seed_admin()` now auto-provisions credits for admin/owner on startup
- **enforce_credits()** helper extracted to `auth_helpers.py`, replacing 4 duplicated credit blocks
- **Dynamic __import__** in provider_health.py → proper import
- **Syntax error** in test_forgot_password.py fixed (dangling line)

## System Status
- 275 routes registered, clean startup
- StockFit returning live SEC data (F-Score, EPS, earnings calendar)
- All linters pass (Python + JS)

## Deployment: PASS — ready for risedual.ai

## Backlog
- P0: Deploy to risedual.ai (pending user production testing)
- P2: QuiverQuant monitoring (blocked on their server stability)
- P2: StockFit 13F holder tracking & SEC filing alerts (pending user API plan upgrade)
- P3: Component splitting (SmartOrderPanel, StrategyBuilder, WaitlistModal, KeyVault)
- P3: Hook dependency audit (all pass ESLint currently)
