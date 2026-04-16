# RISEDUAL AI — Product Requirements Document

## Architecture
- **Frontend**: React + TailwindCSS + Shadcn UI
- **Backend**: FastAPI + MongoDB + APScheduler
- **AI**: Emergent GPT-5.2 + ProviderRouter failover + Financial Tools Agent v2
- **ML**: risedual_core (XGBoost + Platt calibration + 8 pattern detectors + CalibrationGate + 3 autonomous tiers)
- **Payments**: Stripe Live Mode
- **Market Data**: Alpha Vantage -> Finnhub -> TwelveData -> Marketstack
- **Search**: War Room (Registry-based, 12 adapters)
- **Domain**: risedual.ai

## ML Pipeline (LIVE)
- Phase 1: Data collection + labeling (17 features, schema v2)
- Phase 2: 8 pattern detectors enriching every snapshot
- Phase 3: CalibrationGate + Alert Service (Tier 1) + Paper Trader (Tier 2) + Alpaca Broker (Tier 3)
- Status endpoints: GET /api/ml/gate-status, GET /api/ml/stats

## UI: Modals → Inline Tabs (DONE)
- Created PanelShell component (modal/inline dual mode via onClose prop)
- Converted 7 workspace components: Portfolio, Journal, Paper Trading, Bots, Smart Orders, Risk Calculator, Market Scanner
- All render inline within WorkspaceHub tabs
- Still work as modals when opened from other parts of the app (backwards compatible)

## Deployment: LIVE at risedual.ai (278 routes)

## Backlog
- P0: Collect 100+ labeled snapshots → train first model
- P1: Add more workspace tabs (Strategies, Memory, Signals) to inline view
- P1: ML Controls panel with bot toggle switches
