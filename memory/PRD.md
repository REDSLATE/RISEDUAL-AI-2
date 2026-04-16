# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI
- **Backend**: FastAPI + MongoDB + APScheduler
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core (XGBoost + Platt calibration + 8 pattern detectors + regime model + CalibrationGate)
- **Payments**: Stripe Live Mode
- **Market Data**: Alpha Vantage -> Finnhub -> TwelveData -> Marketstack
- **Search**: War Room (Registry-based, 12 adapters)
- **Domain**: risedual.ai

## ML Pipeline — Phase 1: Data Collection (LIVE)
- FeaturesSnapshot capture on every hypothesis (17 features, schema v2)
- Hourly labeler (4h delay, ±1.5% flat band)
- Signal endpoint (`GET /api/signal/{ticker}`) with graceful no_model state
- Training script (`scripts/train_signal_model.py`)

## ML Pipeline — Phase 2: Pattern Detection (LIVE)
- 8 detectors in `risedual_core/ml/patterns.py`
- Enriched into every FeaturesSnapshot via hypothesis_logger

## ML Pipeline — Phase 3: Autonomous Action (BUILT, GATED)
- **CalibrationStats**: Stored during `SignalModel.fit()` — accuracy, Brier, ECE, n_predictions
- **CalibrationGate**: `is_alert_ready()`, `is_paper_trade_ready()`, `is_live_ready()` — pure function tier checks
- **BacktestResult schema** + `scripts/backtest.py` — walk-forward validation with Sharpe, max DD, regime breakdown
- **Tier 1 (Alerts)**: `ml_alert_service.py` — push notifications when confidence >= 60%, regime matches, patterns detected
  - Gate: accuracy >= 55%, n >= 100, ECE < 0.15
- **Tier 2 (Paper Trading)**: `ml_paper_trader.py` — auto paper trades with half-Kelly sizing (cap 25%)
  - Gate: accuracy >= 60%, n >= 500, ECE < 0.12, Sharpe >= 1.0, max DD < 15%
- **Tier 3 (Live Execution)**: `ml_alpaca_broker.py` — Alpaca broker integration with 2% hard cap
  - Gate: accuracy >= 62%, n >= 1000, ECE < 0.10, Sharpe >= 1.2, max DD < 12%, 30 days live, user opt-in
- **Orchestrator**: `routes/ml_orchestrator.py` — runs all 3 tiers after each signal prediction
- **Status endpoints**: `GET /api/ml/gate-status`, `GET /api/ml/stats`

## API Endpoints (278 total)
- `GET /api/signal/{ticker}` — ML signal with autonomous action results
- `GET /api/ml/gate-status` — Current tier readiness
- `GET /api/ml/stats` — Data collection progress, pattern counts, milestones

## Deployment: LIVE at risedual.ai

## Current Status
- 3 snapshots collected, 0 labeled (collecting data)
- All 3 tiers LOCKED (awaiting training data)

## Backlog
- P0: Collect 100+ labeled snapshots → first model training
- P1: Connect Alpaca API keys when ready for Tier 3
