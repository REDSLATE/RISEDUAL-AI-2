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

## Backlog
- P1: Connect Alpaca LIVE API keys via KeyVault (blocked on user account approval)
- P2: StockFit plan upgrade for 13F holder tracking + insider transaction details
- P2: Accumulate 30 live paper trading days for Tier 3 unlock
- P2: QuiverQuant endpoint monitoring (blocked on external provider)
