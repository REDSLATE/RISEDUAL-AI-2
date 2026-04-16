# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI + Recharts
- **Backend**: FastAPI + MongoDB + APScheduler (278+ routes)
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core v6 + XGBoost signal_model_v1 (TRAINED, 62% accuracy, 276K samples)
- **Domain**: risedual.ai

## ML Pipeline Status
- **Signal Model v1**: TRAINED (Apr 16, 2026) — 62% accuracy, 0.245 Brier, 0.090 ECE
- **Tier 1 (Smart Alerts): UNLOCKED** — accuracy 0.620 > 0.55, n=276K > 100, ECE 0.090 < 0.15
- **Tier 2 (Paper Trading): LOCKED** — needs backtest with Sharpe >= 1.0, DD < 15%
- **Tier 3 (Live Execution): LOCKED** — needs Tier 2 + 30 days live + user opt-in
- Data: 276,298 snapshots (276K labeled), 80 tickers, 15 years history

## Historical Backfill (DONE — Apr 16, 2026)
- Script: `/app/backend/scripts/backfill_historical.py`
- 276K+ rows: 50 S&P 500 + 10 ETFs + 20 crypto, daily OHLCV via yfinance
- Indicators: RSI-14, MACD, SMA-20/50, volume ratio (via `ta` library)
- Labels: 1-day and 5-day forward outcomes (up/down/flat)
- Balanced: Up 100K | Down 89K | Flat 87K

## Backlog
- **P0: Run backtest** (`python scripts/backtest.py`) to unlock Tier 2 Paper Trading
- P0: Run pattern detection pass on historical data
- P1: Connect Alpaca API keys via KeyVault (once Tier 3 unlocked)
- P2: StockFit 13F holder tracking & SEC filing change alerts
