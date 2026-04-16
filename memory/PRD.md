# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI (inline tabs, no modals for workspace)
- **Backend**: FastAPI + MongoDB + APScheduler (278 routes)
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core (XGBoost + Platt calibration + 8 pattern detectors + CalibrationGate + 3 autonomous tiers)
- **Domain**: risedual.ai

## ML Pipeline (LIVE — collecting data)
- Phase 1: FeaturesSnapshot capture (17 features, schema v2)
- Phase 2: 8 pattern detectors (2 detections so far: rsi_divergence, macd_crossover)
- Phase 3: CalibrationGate + Alerts + Paper Trader + Alpaca Broker (all gated)

## ML Controls Panel (NEW)
- Tier gate cards (3 tiers with lock/unlock + requirements)
- Data collection progress bars (100/500/1000 milestones)
- Model status (trained/collecting with accuracy stats)
- Pattern detection heatmap (8 detectors with counts)
- Autonomous activity counters (alerts, paper trades, live trades)
- Milestone tracker (5 checkpoints)

## Workspace: Modals → Inline Tabs (DONE)
- PanelShell dual-mode component
- 7 components converted: Portfolio, Journal, Paper, Bots, Orders, Risk, Scanner
- ML Controls added as dedicated tab
- 12 tabs total in WorkspaceHub

## Backlog
- P0: Collect 100+ snapshots → first training
- P1: Convert remaining modals (Strategies, Memory, Signals, Credits, Auth) to inline tabs
