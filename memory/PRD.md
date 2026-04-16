# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI + Recharts (inline tabs, no modals for workspace)
- **Backend**: FastAPI + MongoDB + APScheduler (278+ routes)
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core v6 (XGBoost + Platt calibration + 8 pattern detectors + typed CalibrationGate + 3 autonomous tiers + Kelly position sizing)
- **Domain**: risedual.ai

## ML Pipeline (LIVE — 276K labeled snapshots)
- Phase 1: FeaturesSnapshot capture (17 features + 8 pattern booleans, schema v2/v3)
- Phase 2: 8 pattern detectors
- Phase 3 (v6): Typed CalibrationGate, half-Kelly sizing, VAPID push, stateless orchestrator

## Historical Backfill (DONE — Apr 16, 2026)
- Script: `/app/backend/scripts/backfill_historical.py` (from v7 zip)
- Source: yfinance (free, no API key) + ta library for indicators
- **276,298 total snapshots** in `features_snapshots` collection
- **276,000 labeled** with 1-day and 5-day forward outcomes
- **80 tickers**: 50 S&P 500 + 10 ETFs + 20 crypto
- **15 years** of daily OHLCV for equities, 5-8 years crypto
- Outcome distribution: Up 100,031 | Down 89,033 | Flat 86,936
- Schema version 3 with fields: rsi_14, macd, macd_signal, sma_20, sma_50, volume_ratio, outcome_1d, outcome_5d, return_1d, return_5d
- Pattern detection skipped on initial backfill (--skip-patterns) for speed
- Safe to re-run: upsert on (ticker, timestamp) composite key

## ML v6 Upgrade (DONE — Apr 16, 2026)
- Services: ml_alert_service, ml_paper_trader, ml_alpaca_broker, ml_orchestrator
- Routes: gate-status, stats, paper-trades, calibration-curve
- risedual_core: calibration (Tier/GateResult), signal_model (CalibrationStats), schemas (patterns)

## ML Controls Enhancements (DONE — Apr 16, 2026)
- Paper Trading PnL Dashboard (MLPaperPnL.jsx)
- Calibration Curve Visualization (CalibrationChart.jsx)

## Mobile Fix — Market Scanner (DONE — Apr 16, 2026)
- Fixed two-column layout overlapping on mobile

## Backlog
- P0: Train first signal model (276K+ labeled snapshots available — READY!)
- P0: Run pattern detection pass on historical data (--tickers only with patterns)
- P1: Connect Alpaca API keys via KeyVault (once Tier 3 unlocked)
- P2: StockFit 13F holder tracking & SEC filing change alerts
