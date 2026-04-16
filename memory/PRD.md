# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI + Recharts
- **Backend**: FastAPI + MongoDB + APScheduler (278+ routes)
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core v6 + signal_model_v4 (62.6% acc, Sharpe 1.25, DD 11.7%)
- **Domain**: risedual.ai

## ML Pipeline Status (Apr 16, 2026)
- **Signal Model v4**: 62.11% accuracy, 0.012 ECE, 276K samples
- **Regime Model v1**: KMeans (SPY/QQQ/IWM), current: SIDEWAYS
- **Tier 1 (Smart Alerts): UNLOCKED**
- **Tier 2 (Paper Trading): UNLOCKED** — Sharpe 1.25, DD 11.7%
- **Tier 3 (Live Execution): LOCKED** — needs 30 live days + user opt-in

## Backtest Strategy: Pattern-Concentrated + Regime-Aligned
- **Sharpe 1.25** | DD 11.7% | Win Rate 55.3% | 3,730 trades | +232% cumulative
- High-alpha patterns: rsi_divergence (conf_floor 0.38), head_and_shoulders (0.35)
- Medium-alpha: volume_surge (0.42), bull_flag (0.44)
- EXCLUDED: double_bottom (42.2% WR, net drag in backtest)
- Position concentration: 2.2x for RSI divergence in sideways/bear, 2.0x for h&s
- Per-trade stop-loss: -2% max
- Bull regime de-risked: 0.8x multiplier (weak PnL in backtest)
- Bear regime concentrated on pattern signals: rsi_divergence+bear = top combo (+0.30 PnL)

## Pattern Alpha (from backtest diagnostics)
- rsi_divergence: 884 trades, 55.7% WR, +0.68 PnL (alpha engine)
- bull_flag: 157 trades, 46.5% WR, +0.35 PnL (works in sideways/bear, avoid bull)
- volume_surge: 493 trades, 53.1% WR, +0.10 PnL
- head_and_shoulders: 0 trades in eval (too rare — but 33% training lift = holy grail)
- double_bottom: REMOVED — 42.2% WR, -0.015 PnL

## Data Pipeline
- 276,298 snapshots, 80 tickers, 15 years, 98.2% regime coverage, 57,854 patterns

## Backlog
- P1: Connect Alpaca API keys via KeyVault
- P2: StockFit 13F holder tracking
- P2: Accumulate 30 live days for Tier 3
