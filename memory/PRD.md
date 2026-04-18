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

## AI Agent Delegation + Smart Money Score (COMPLETED Apr 18)
- Added sparkle ✨ button to each watchlist row (`watchlist-delegate-ai-{SYMBOL}` test ID)
- **Extended to 13F tables**: sparkle ✨ button on every ticker cell in both Holdings table and QoQ Changes table of the 13F Research Hub tab
- Context-aware prompts:
  - Watchlist: _"Analyze {SYMBOL}: current price, technical levels, recent news, latest 13F moves"_
  - 13F Holdings cell: _"Walk me through {SYMBOL}: current price, recent performance, technical setup, why major institutions hold it"_
  - 13F Changes cell: _"Why did {INSTITUTION} {verb} its position in {SYMBOL} last quarter?"_ (verb = open/exit/increase/trim)
- Click → dispatches `risedualai-open-chat` event with `{prefill, autoSend: true}` payload, chat fires `risedualai-autosend` after 400ms settle

## Smart Money Regime-Shift Alerts (COMPLETED Apr 18)
- Added `services/sec_13f_service.snapshot_smart_money_scores()` — writes daily `{symbol, date, score, signal, counts}` rows to `smart_money_scores` (idempotent per UTC day).
- Added `detect_smart_money_shifts(db, symbols, threshold=10)` — diffs new score vs. previous snapshot, creates `{type: 'smart_money_shift', prev_score, new_score, delta, signal_change, top_movers[]}` in `sec_13f_alerts`, broadcasts VAPID push notification titled _"AAPL Smart Money: 20 ↗ 44"_ with body _"+24 pts · now neutral · BlackRock increased"_.
- Integrated into existing `scan_and_alert` → runs automatically at **08:00 UTC daily** right after the 13F refresh job, scanning every symbol that appears in any user's watchlist.
- New endpoints:
  - `GET /api/stockfit/13f/smart-money-history/{symbol}?days=30` — chronological SMS timeline per symbol
  - `POST /api/stockfit/13f/smart-money-scan?threshold=10` — admin-only manual trigger
- **Frontend**: compact colored shift banners in the Watchlist panel above the ticker list (green for up-shifts, red for down-shifts). Each banner shows `SYMBOL Smart Money {prev} ↗/↘ {new} ({delta} pts) now {signal}`. **Click any banner** → delegates to AI with a regime-shift prompt that includes the top mover's institution and action.
- Verified end-to-end: seeded fake Apr-17 baseline (AAPL=20, NVDA=80), ran scan → 2 alerts created (AAPL +24, NVDA -22), both banners rendered in UI, click → chat opened with contextual prompt auto-sent. 0 JS errors.
- New `services/sec_13f_service.compute_smart_money_score(db, symbol)` — aggregates QoQ position changes across tracked institutions, weighted by `log10(AUM/$1B + 1)` so BlackRock/Vanguard don't dominate but still count more than smaller funds. Maps signed-weighted-sum to 0–100 where 50 = neutral, ≥60 = bullish institutional consensus, ≤40 = bearish.
- Endpoints: `GET /api/stockfit/13f/smart-money-score/{symbol}`, `GET /api/stockfit/13f/smart-money-scores?symbols=AAPL,NVDA,...` (batch, 50 max)
- Returns: `{score, signal, bullish_count, bearish_count, holder_count, net_flow_usd, total_value_usd, contributors[]}`
- **Watchlist integration**: color-coded SM badge on each row (green ≥60, amber 40-60, red ≤40, gray = insufficient data). Hover tooltip explains the score and shows bullish/bearish counts. **Click the badge** → delegates to AI with a prompt that includes the score and counts so the AI can explain the institutional thesis.
- Verified live: AAPL=44 (BlackRock/Vanguard adding, Berkshire exited -4.3%), NVDA=58, TSLA=56, MSFT=49, META=no data.

## CUSIP→Ticker Mapping Upgrade (COMPLETED Apr 18)
- **Integrated OpenFIGI API** (free, no key needed at 25 req/min; free key bumps to 250 req/6s — settable via `OPENFIGI_API_KEY` env var)
- Added `services/cusip_mapper.py` with:
  - Persistent MongoDB caching (`cusip_ticker_map` collection — stores `{cusip, ticker, name, exchange, figi, security_type, resolved_at}`)
  - Negative-caching for unresolvable CUSIPs so we don't retry
  - Auto-detects API key to pick the right batch size (10 anon / 100 keyed) and rate
- Wired new `/api/stockfit/13f/backfill-cusips?top_only=true` admin endpoint + `/coverage` stats endpoint
- Rewrote `get_holders_of_symbol` to use CUSIP-based primary match (falls back to fuzzy name match only when no CUSIP coverage)
- Fixed name-normalization bug: `" LIMITED"` and `" INCORPORATED"` were missing from suffix strip list; also reordered to longer-first to prevent `" INC"` swallowing `" INCORPORATED"` partially
- **Result**: Berkshire Q4-2025 top 25 holdings → **25/25 tickers resolved (100%)**. BlackRock top 10 → 10/10. Full database coverage: **6944/7843 CUSIPs (88.5%)** — remaining 11.5% are genuinely unmappable (foreign warrants, bonds, delisted securities that never appear in UI top-holdings views).
- Backfill runtime: ~2.5 min for full 7843 CUSIPs with key (vs. 30+ min anon).
- Env var `OPENFIGI_API_KEY` added to `/app/backend/.env`.

## Code Quality Refactoring (COMPLETED Apr 18)
- **AlpacaOAuthDemo.jsx** (830 lines) decomposed into `oauth-demo/` folder: `DemoShared.jsx`, `StepLanding.jsx`, `StepDashboard.jsx`, `StepBrokerConnect.jsx`, `StepDisclosure.jsx`, `StepAlpacaAuth.jsx`, `StepSuccessRevoke.jsx`
- **SmartOrderPanel.jsx** (440 lines) decomposed into `smart-orders/SmartOrderList.jsx` and `smart-orders/SmartOrderPreview.jsx`
- Fixed bug: previous session had created SmartOrderList/SmartOrderPreview files but didn't actually wire them into SmartOrderPanel (orders tab would have crashed due to missing Badge/ModeTag/StatusTag/Trash2 imports). Now properly integrated.
- **RiseDualGPTChat.jsx** (399 → 250 lines, -37%) decomposed into:
  - `hooks/useTTS.js` (51 lines) — text-to-speech playback
  - `hooks/useStreamingAgent.js` (106 lines) — SSE-based tool-call agent
  - `components/chat/AgentTrace.jsx` (48 lines) — live agent-working UI
- Verified: OAuth 7-step flow, Smart Orders Create/List/Preview, Chat standard path ("Chat works."), Chat streaming agent path (CAGR calc) — all render with 0 JS errors.

## Python Type Hint Coverage (COMPLETED Apr 18)
- **Actual measured coverage**: 59.9% return-type, **91.1% parameter-type** (much better than handoff's "<50%" claim)
- New code (`sec_13f_service.py`, `stockfit_13f.py` routes): **100% return-type coverage**
- Decided: blanket-hinting 559 existing `routes/` functions is high-risk/low-value since FastAPI uses Pydantic `response_model` for runtime validation anyway. Focus going forward: type-hint all new code at 100%.

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
- P1: Connect Alpaca LIVE API keys via KeyVault (blocked on user account approval)
- P2: Accumulate 30 live paper trading days for Tier 3 unlock
- P2: QuiverQuant endpoint monitoring (blocked on external provider)
