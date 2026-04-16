# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI + Recharts
- **Backend**: FastAPI + MongoDB + APScheduler (278+ routes)
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core v6 + signal_model_v4 (62.11% acc, 0.012 ECE, 276K samples, enriched with patterns+regimes)
- **Domain**: risedual.ai

## ML Pipeline Status (Apr 16, 2026)
- **Signal Model v4**: 62.11% accuracy, 0.234 Brier, 0.012 ECE
- **Regime Model v1**: KMeans (SPY/QQQ/IWM), current regime: SIDEWAYS
- **Tier 1 (Smart Alerts): UNLOCKED**
- **Tier 2 (Paper Trading): LOCKED** — Sharpe -0.0005 < 1.0 (trade frequency too low)
- **Tier 3 (Live Execution): LOCKED**

## Data Pipeline
- 276,298 snapshots, 276,000 labeled (1d+5d outcomes)
- 80 tickers (50 S&P 500 + 10 ETFs + 20 crypto), 15 years history
- 98.2% regime coverage (271,399 rows labeled bull/bear/sideways)
- 57,854 pattern detections across 8 types
- Balanced outcomes: Up 100K | Down 89K | Flat 87K

## Pattern Alpha Analysis
- rsi_divergence: +9.0% lift (45.1% vs 36.2% baseline)
- head_and_shoulders: +33.0% lift (small sample)
- double_bottom: +5.2% lift
- volume_surge: +3.8% lift
- bull_flag: +2.1% lift

## Scripts
- `scripts/backfill_historical.py` — yfinance + ta indicators
- `scripts/train_signal_model.py` — v8, chronological split, multi-schema
- `scripts/train_regime_model.py` — yfinance + KMeans/HMM
- `scripts/backfill_regimes.py` — bulk regime label update
- `scripts/backfill_patterns.py` — pattern detection across all tickers
- `scripts/backtest.py` — walk-forward PnL simulation

## Backlog
- P1: Improve backtest Sharpe (tune position sizing / trade frequency to unlock Tier 2)
- P1: Connect Alpaca API keys via KeyVault
- P2: StockFit 13F holder tracking
- P2: QuiverQuant endpoint monitoring
