# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI + Recharts
- **Backend**: FastAPI + MongoDB + APScheduler (278+ routes)
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
- **Pro Tier** (5,000 calls/day): + /api/v1/ml-signal/{ticker}, /api/v1/ml-stats, /api/v1/sectors, /api/v1/signals/war-room/{symbol}, /api/v1/research/{symbol}, /api/v1/search, /api/v1/provider-status
- **Enterprise Tier** (50,000 calls/day): + POST /api/v1/ml-signal/batch, /api/v1/paper-trades, /api/v1/ml-stats (backtest access)
- **Auth**: X-API-Key header for public endpoints, JWT Bearer for developer dashboard
- **CORS**: X-API-Key header whitelisted
- **Testing**: 20/20 backend tests passed (iteration 132)

## Data Pipeline
- 276,298 snapshots, 80 tickers, 15 years, 98.2% regime coverage, 57,854 patterns

## Backlog
- P1: Connect Alpaca LIVE API keys via KeyVault (blocked on user account approval)
- P2: StockFit 13F holder tracking + SEC filing change alerts
- P2: Accumulate 30 live paper trading days for Tier 3 unlock
- P2: QuiverQuant endpoint monitoring (blocked on external provider)
