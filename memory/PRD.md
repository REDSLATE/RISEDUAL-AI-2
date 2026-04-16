# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI + Recharts
- **Backend**: FastAPI + MongoDB + APScheduler (278+ routes)
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core v6 + signal_model_v3 (TRAINED, 62.09% acc, 276K samples, chronological split)
- **Domain**: risedual.ai

## ML Pipeline Status
- **Signal Model v3**: 62.09% accuracy, 0.234 Brier, 0.013 ECE (chronological eval — no look-ahead)
- **Tier 1 (Smart Alerts): UNLOCKED**
- **Tier 2 (Paper Trading): LOCKED** — accuracy gate passed, needs backtest Sharpe/DD
- **Tier 3 (Live Execution): LOCKED**
- Data: 276,298 snapshots, 80 tickers, 15 years, balanced outcomes

## v8 Upgrade (DONE — Apr 16, 2026)
- `train_signal_model.py` — v8: chronological split, multi-schema support, --target flag, per-regime/pattern accuracy, gate readiness report
- `backfill_regimes.py` — NEW: labels regime_label on all snapshots using trained RegimeModel
- `GET /api/ml/backfill-status` — NEW: backfill progress, ticker coverage, schema breakdown, regime coverage, readiness milestones

## Backlog
- **P0: Run backtest** (`python scripts/backtest.py`) to unlock Tier 2
- P0: Train regime model + run `backfill_regimes.py` for regime labels
- P0: Run pattern detection pass on historical data
- P1: Connect Alpaca API keys via KeyVault
- P2: StockFit 13F holder tracking
