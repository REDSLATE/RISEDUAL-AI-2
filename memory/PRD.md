# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI + Recharts
- **Backend**: FastAPI + MongoDB + APScheduler (280+ routes)
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core v6 + signal_model_v4 (62.6% acc, Sharpe 1.56, DD 11.2%)
- **Domain**: risedual.ai

## ML Pipeline Status (Apr 16, 2026)
- **Signal Model v4**: 62.11% accuracy, 0.012 ECE, 276K samples
- **Regime Model v1**: KMeans (SPY/QQQ/IWM), current: SIDEWAYS
- **Tier 1 (Smart Alerts): UNLOCKED**
- **Tier 2 (Paper Trading): UNLOCKED** — Sharpe 1.56, DD 11.2%
- **Tier 3 (Live Execution): LOCKED** — needs 30 live days + user opt-in

## Backtest Strategy: Pattern-Concentrated + Regime-Aligned
- **Sharpe 1.56** | DD 11.2% | Win Rate 55.3% | 3,730 trades | +232% cumulative
- High-alpha patterns: rsi_divergence (conf_floor 0.38), head_and_shoulders (0.35)
- Medium-alpha: volume_surge (0.42), bull_flag (0.44)
- EXCLUDED: double_bottom (42.2% WR, net drag in backtest)
- Per-trade stop-loss: -2% max

## Public Developer API (COMPLETED Apr 16, 2026)
- **Key Management**: POST /api/developer/keys/generate, GET /api/developer/keys, DELETE /api/developer/keys/{key_id}
- **Usage Tracking**: GET /api/developer/usage (daily usage, tier info, 7-day history)
- **Free Tier** (100 calls/day): /api/v1/predictions, /api/v1/quote/{symbol}, /api/v1/watchlist, /api/v1/headlines, /api/v1/market/sectors
- **Pro Tier** (5,000 calls/day): + /api/v1/ml-signal/{ticker}, /api/v1/ml-stats, /api/v1/sectors, /api/v1/fundamentals/{symbol}, /api/v1/signals/war-room/{symbol}, /api/v1/research/{symbol}, /api/v1/search, /api/v1/provider-status
- **Enterprise Tier** (50,000 calls/day): + POST /api/v1/ml-signal/batch, /api/v1/paper-trades, /api/v1/ml-stats (backtest access)
- **Testing**: 20/20 backend tests passed (iteration 132)

## StockFit Fundamentals (COMPLETED Apr 16, 2026)
- **Backend**: /api/stockfit/fundamentals/{symbol} (aggregated), /api/stockfit/income/{symbol}, /api/stockfit/balance-sheet/{symbol}, /api/stockfit/scores/{symbol}, /api/stockfit/earnings/{symbol}
- **Frontend**: New "StockFit" tab in Research Hub — shows Piotroski F-Score gauge, Altman Z-Score, EPS, Revenue Growth, Margins, Piotroski Checklist, 3-year Income Statement table, 3-year Balance Sheet table
- **War Room**: StockFit adapter updated to use only free-tier endpoints (removed 403-producing insider endpoints)
- **Public API**: /api/v1/fundamentals/{symbol} (Pro tier required)
- **API Key**: fl_DLLSJAY7SWDG (free tier — financials, balance sheet, scores, earnings available; insider/13F/calendar blocked)
- **Testing**: 19/19 backend + full frontend passed (iteration 133)

## Data Pipeline
- 276,298 snapshots, 80 tickers, 15 years, 98.2% regime coverage, 57,854 patterns

## Backlog
- P1: Connect Alpaca LIVE API keys via KeyVault (blocked on user account approval)
- P2: StockFit plan upgrade for 13F holder tracking + insider transaction details
- P2: Accumulate 30 live paper trading days for Tier 3 unlock
- P2: QuiverQuant endpoint monitoring (blocked on external provider)
