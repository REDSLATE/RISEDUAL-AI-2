# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI + Recharts
- **Backend**: FastAPI + MongoDB + APScheduler (285+ routes)
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core v6 + signal_model_v4 (62.6% acc, Sharpe 1.56, DD 11.2%)
- **Domain**: risedual.ai

## ML Pipeline Status (Apr 16, 2026)
- **Signal Model v4**: 62.11% accuracy, 0.012 ECE, 276K samples
- **Tier 1 (Smart Alerts): UNLOCKED**
- **Tier 2 (Paper Trading): UNLOCKED** — Sharpe 1.56, DD 11.2%
- **Tier 3 (Live Execution): LOCKED** — needs 30 live days + user opt-in

## Public Developer API (COMPLETED Apr 16)
- Key Management, Usage Tracking, Stripe tier gating (Free/Pro/Enterprise)
- 20/20 backend tests passed (iteration 132)

## StockFit Fundamentals (COMPLETED Apr 16)
- SEC EDGAR data: Income Statement, Balance Sheet, F-Score, Z-Score, Earnings
- New "StockFit" tab in Research Hub
- 19/19 backend + full frontend passed (iteration 133)

## FRED + ALFRED Integration (COMPLETED Apr 16)
- **FRED Economy tab** in Macro Dashboard — 15 curated indicators across 7 categories (Rates, Growth, Inflation, Employment, Housing, Consumer, Trade)
- **ALFRED Vintage Compare** — hover any indicator → clock icon → see original vs. revised values across 3 vintage dates
- **Revision Detection** — `/api/fred/revisions` auto-detects when data is revised vs. stored snapshot
- **Daily Snapshots** — 7 AM UTC cron job stores all indicators + ALFRED vintages for revision-watch series (GDP, GDPC1, CPI, Payrolls, Unemployment, Housing Starts, Trade Balance) to `fred_snapshots` MongoDB collection
- **FRED Search** — search across 840K+ FRED series
- **Release Browser** — browse all series in a FRED release
- **API Key**: eb209d607599b49efa6e4409a218f7b6

## Crypto Banner (COMPLETED Apr 16)
- Scrolling ticker with top 15 coins (BTC, ETH, BNB, SOL, XRP, ADA, DOGE, AVAX, DOT, MATIC, LINK, SHIB, LTC, UNI, ATOM)
- requestAnimationFrame scroll matching stock ticker

## Data Pipeline
- 276,298 ML snapshots, 80 tickers, 15 years, 98.2% regime coverage, 57,854 patterns
- FRED snapshots accumulating daily (first snapshot: Apr 16, 2026)

## Code Quality Refactoring (COMPLETED Apr 18)
- **AlpacaOAuthDemo.jsx** (830 lines) decomposed into `oauth-demo/` folder: `DemoShared.jsx`, `StepLanding.jsx`, `StepDashboard.jsx`, `StepBrokerConnect.jsx`, `StepDisclosure.jsx`, `StepAlpacaAuth.jsx`, `StepSuccessRevoke.jsx`
- **SmartOrderPanel.jsx** (440 lines) decomposed into `smart-orders/SmartOrderList.jsx` and `smart-orders/SmartOrderPreview.jsx`
- Fixed bug: previous session had created SmartOrderList/SmartOrderPreview files but didn't actually wire them into SmartOrderPanel (orders tab would have crashed due to missing Badge/ModeTag/StatusTag/Trash2 imports). Now properly integrated.
- Verified: All 7 OAuth demo steps + Smart Orders Create/List/Preview views render with 0 JS errors.

## 13F Holder Tracking + SEC Filing Alerts (COMPLETED Apr 18)
- **SEC EDGAR direct integration** — free, authoritative, no paywall (StockFit's fund endpoints require paid plan)
- **25 top institutions** seeded: Berkshire, BlackRock, Vanguard, State Street, Renaissance, Citadel, Bridgewater, Two Sigma, Millennium, Point72, D.E. Shaw, AQR, Tiger Global, Coatue, ARK, Fidelity (FMR), T. Rowe Price, Wellington, Northern Trust, Invesco, Morgan Stanley, JPMorgan, BofA, Goldman, Geode
- **Backend service**: `/app/backend/services/sec_13f_service.py` — fetches 13F-HR filings, parses INFORMATION TABLE XML, auto-detects thousands→USD value normalization (SEC switched format 2022-Q4), CUSIP→ticker mapping via SEC company_tickers.json, name-based fuzzy match for issuer→ticker
- **Endpoints** `/api/stockfit/13f/*`:
  - `GET /institutions` — list tracked institutions with latest filing metadata
  - `GET /institution/{cik}` — aggregated top holdings (CUSIP-deduplicated) for latest quarter
  - `GET /holders/{symbol}` — which tracked institutions hold a stock (excludes PUT/CALL derivatives)
  - `GET /changes/{cik}` — QoQ diff: new / exited / increased / decreased positions
  - `GET /alerts` — user-specific 13F alerts based on their watchlist (auth required)
  - `POST /refresh` — admin-only, force refresh (per-CIK or all)
- **Alert triggers** (auto-fired by daily scheduler at 08:00 UTC for watchlist symbols):
  - NEW position ≥ $50M
  - EXITED position (was previously held)
  - INCREASED by ≥ 25% AND ≥ $100M position
  - DECREASED by ≥ 25% AND prev value ≥ $100M
- **Notification fan-out**: broadcasts via existing `broadcast_notification` (in-app + VAPID push)
- **Frontend**: new "13F Holders" tab in Research Hub → `StockFit13F.jsx` with two modes (Lookup by Symbol / Browse by Institution), aggregated holdings table, QoQ changes table with type badges
- **Data verified**: Berkshire Q4 2025 AAPL=227.9M sh/$62.0B, AXP $56B, BAC $28B, KO $28B, CVX $20B. AAPL holders: Vanguard ($387B), BlackRock ($221B), Fidelity ($83B), Berkshire ($62B).
- **21/21 backend tests passed** (iteration 134), 0 JS errors, 0 `_id` leaks in responses.

## Backlog
- P1: Decompose `RiseDualGPTChat.jsx` (370 lines)
- P1: Improve Python backend type hint coverage (<50%)
- P1: Connect Alpaca LIVE API keys via KeyVault (blocked on user account approval)
- P2: Improve CUSIP→ticker mapping (currently ~85% coverage; add SEC 13F Securities list for 100% coverage)
- P2: Accumulate 30 live paper trading days for Tier 3 unlock
- P2: QuiverQuant endpoint monitoring (blocked on external provider)
