# RISEDUAL AI — PRD

## Original Problem Statement
Build a functional clone of a trading app named **RISEDUAL AI**. Multi-model AI consensus, Realtime P&L Tracker, Thread-Safe Native Multi-Agent Engine, Live Order Flow Heatmaps, Paper Trading capabilities, Global Safety Kill-Switch System, Multi-broker Live Options Trading flow, advanced Research Shadow Layer for ML adaptation, "Dual-Stack Architecture", and a "Market State Awareness" Terminal UI.

## Architecture
- **FastAPI** backend on `:8001` with `/api` prefix
- **React** frontend on `:3000`
- **MongoDB** for persistence + ChromaDB for vector memory (Shelly)
- **8-ML neuro-symbolic stack** (`RisedualMLPipeline`) sitting alongside the LLM Council
- **Closed-loop RoadGuard pair**: dedicated capital governor per executor lane
- All risk-modulating layers operate in shadow mode until calibration delta logs prove safe

### Canonical pipeline flow
```
market data
  -> Shelly recall (memory consult; observation only)
  -> perception ML (6 sklearn models + 5-rule symbolic engine)
  -> strategist ML
  -> auditor ML
  -> fast_veto ML (veto-only)
  -> shadow ML (observe only)
  -> executor ML  (lane-routed: EquityExecutorML | CryptoExecutorML)
  -> Shelly remember (organic episode)
  -> RoadGuard pair (closed loop):
        equity intent -> EquityRoadGuard  -> roadguard_equity_decisions
        crypto intent -> CryptoRoadGuard  -> roadguard_crypto_decisions
  -> [broker — disabled]
```

## What's Implemented (this fork — 2026-05-08)

### 8-ML stack rebuild + closed-loop RoadGuard pair (Phase 0–5a)

**Phase 0** — `services/ml/` boundary scaffold: `FeatureFrame`, `MLVerdict`, `ModelBootReceipt`, `LaneDisabledError`, sealed `ShadowMLLayer`, fail-fast `BaseMLLayer`. Boot receipts registry per process.

**Phase 1** — Perception ML: 6 sklearn sub-models + symbolic engine.
  - EventShockModel, RegimeStateModel, DrawdownDistanceModel, LiquidityModel, SystemHealthModel, PacingModel
  - All artifact-gated (env vars: `PERCEPTION_*_ARTIFACT`); missing artifact → `LaneDisabledError` → `NO_TRADE`
  - Domain-informed dummy training (RandomForest binary, Random Forest multi-class, Random Forest regressor)
  - 5 symbolic rules: `R1 SYSTEM_DEGRADED`, `R2 EVENT_SHOCK_ACTIVE`, `R3 LIQUIDITY_THIN`, `R4 DRAWDOWN_DEEP`, `R5 REGIME_RISK_OFF`
  - Feature extractors normalise inputs to `[0, 1]`

**Phase 2** — Two distinct executor MLs:
  - `EquityExecutorML` (lane="equity", artifact env `EQUITY_EXECUTOR_ARTIFACT`)
  - `CryptoExecutorML`  (lane="crypto",  artifact env `CRYPTO_EXECUTOR_ARTIFACT`)
  - LANE_MISMATCH gate prevents cross-lane signal flow
  - Respects upstream fast_veto verdict; passthrough on AUDITOR_CONFIRM

**Phase 3** — Pipeline orchestration: `StrategistML`, `AuditorML`, `FastVetoMLLayer` (veto-only), `ShadowML` (observe-only), `RisedualMLPipeline.decide()` runs the canonical 8-step chain.

**Phase 4 — Closed-loop RoadGuard pair (per user spec)**:
  - `EquityRoadGuard` — dedicated to equity executor; reads `ROADGUARD_EQUITY_*` envs; writes to `roadguard_equity_decisions`
  - `CryptoRoadGuard` — dedicated to crypto executor; reads `ROADGUARD_CRYPTO_*` envs; writes to `roadguard_crypto_decisions`
  - Each pair has its own enforce flag (`*_ENFORCE_ENABLED`)
  - 11 gates per RG: G00 (LANE_MISMATCH), G01 broker health, G02 daily loss, G09 min ticket, G03 total exposure, G04 lane exposure, G05/G06 max positions, G07 duplicate symbol, G08 cash floor, G10 kill-switch
  - Both PASS/REDUCE/BLOCK only — `ROADGUARD_CAN_APPROVE = False` invariant
  - `RoadGuardV2` dispatcher kept for back-compat (routes by `intent.lane`)

**Phase 4.5** — Shelly client: `ShellyClient` adapter on top of `services/market_memory_service.py`. `recall(frame)` returns `ShellyRecall` summary; `remember(frame, verdict)` persists `source="organic"` episodes. **No `.veto`/`.size`/`.execute`/`.place_order`/`.request_order` methods** — pinned by tests.

**Phase 5a — Receipt-only wiring**:
  - `services/ml/shadow_wiring.py` — fire-and-forget hook called from `trading_bot_service.execute_signal`
  - Builds `FeatureFrame` from live signal + market_data, runs the 8-ML pipeline, evaluates the matching lane RG, writes ONE `alpha_decision_log` receipt + ONE lane-specific RG decision row
  - **NEVER calls a broker. NEVER places an order.** `will_hit_live_broker=False` hard-coded
  - Admin endpoints: `/api/admin/ml/v2/{boot-receipts, decisions/summary, decisions/recent, pipeline/decide, roadguard/pair-status, roadguard/decisions/recent}`

### Alpha helpers (observability primitives, independent of ML stack)
- `services/alpha_decision_log.py` — 8-stage NO_TRADE receipts, 30-day TTL, `record_decision()` + `summary()`
- `services/alpha_weight_calibrator.py` — sizes from `min(equity, cash)`, **NEVER** buying_power. `$500` abs cap / 5 slots → `$100`/trade default
- `services/alpha_daily_mandate.py` — ≥20 round-trips/day target, 0.70→0.60 pressure curve, `should_block_new_open(15:30 ET)`, `should_force_flat(15:55 ET)`

### Tests
**Total: 199 tests passing** (60 ML pipeline + 16 lane-pair RG + 13 alpha helpers + 16 boundary + 11 perception + existing suite preserved)

Files:
- `tests/test_ml_boundary.py`
- `tests/test_ml_perception.py`
- `tests/test_ml_pipeline_v2.py`
- `tests/test_roadguard_pair.py` (closed-loop pair invariants)
- `tests/test_alpha_helpers.py`

## Critical Invariants (do not break)
1. Alpha must size from `min(equity, cash)`. **NEVER** buying_power.
2. Daily mandate: ≥20 round-trips, flat-by-15:55-ET. Equities only.
3. Receipts validate strictly: `decision=NO_TRADE` requires `blocked_at` ∈ 8-stage chain.
4. `FAST_VETO_ENFORCE_ENABLED`, `ROADGUARD_EQUITY_ENFORCE_ENABLED`, `ROADGUARD_CRYPTO_ENFORCE_ENABLED`, `*_EXECUTOR_ENFORCE_ENABLED` all stay `false` until calibration proven.
5. **`BROKER_LIVE_ORDER_ENABLED=false`** until Phase 5a receipts prove clean.
6. `ROADGUARD_CAN_APPROVE=False` — both lane RGs NEVER create trades or increase size.
7. Shadow ML cannot veto/size/sell/execute/place_order/request_order. Sealed at the type level.
8. **Shelly cannot expose `.veto()`, `.size()`, `.execute()`, `.place_order()`, `.request_order()`** — pinned by `test_shelly_client_has_no_authority_methods`.
9. Closed-loop RG pair: an equity intent that arrives at `CryptoRoadGuard` (or vice versa) MUST fail LANE_MISMATCH at G00. No cross-contamination between `roadguard_equity_decisions` and `roadguard_crypto_decisions` collections — pinned by tests.
10. Shadow wiring NEVER calls a broker — `will_hit_live_broker` is hard-coded `False`.
11. `yfinance` is rejected as a market data source.
12. Shelly recall/remember failures NEVER abort the pipeline.

## Pending / Backlog

### P0 — Next session
- **Phase 5b** — Broker wiring behind enforce flags (only after 5a receipts look clean):
  - `EXECUTOR_ENFORCE_ENABLED` per lane; `BROKER_LIVE_ORDER_ENABLED=true` only after operator approval
  - Wire `compute_pressure().adjusted_threshold` into the THRESHOLD gate
  - Wire `should_force_flat()` into 15:55 ET force-flat job

### P1
- Real `Strategist` and `Auditor` ML implementations (replace placeholders)
- Real perception feature extraction from live market data (replace dummy training)
- Camaro Terminal UI (`/terminal/:symbol`) — blocked on broker decision (Webull vs Kraken Equities) and user-provided WebSocket keys
- Camaro→Shelly bridge: ingest Camaro trades into ChromaDB tagged `source="camaro"`
- `OPS_ALERT_WEBHOOK_URL` config in production
- Admin Health-dashboard tiles: ML pipeline, RG pair, mandate, Shelly

### P2
- `_start_schedulers()` strangler split into `services/scheduler_jobs/*.py`
- `SwallowedExceptionMonitor` to catch silent background-job failures
- Train + persist real `.joblib` artifacts for the 6 perception sub-models + 2 executor MLs
- Strangler split of large IP files (e.g., `crypto_paper_trader.py`)

### Backlog
- Alpha rollout 4 & 5: shadow → enforce after delta-log review
- GitHub overlay flake (parked)
- 16:30 ET daily mandate digest email

## Key Endpoints (new)
- `GET  /api/admin/ml/v2/boot-receipts` — per-layer ready/disabled
- `GET  /api/admin/ml/v2/decisions/summary?days=7` — receipts by stage
- `GET  /api/admin/ml/v2/decisions/recent?limit=50&blocked_at=&symbol=`
- `POST /api/admin/ml/v2/pipeline/decide?record=` — synthetic dry-run
- `GET  /api/admin/ml/v2/roadguard/pair-status` — closed-loop RG pair counts + last verdict
- `GET  /api/admin/ml/v2/roadguard/decisions/recent?lane=equity|crypto&limit=50`

## Test Credentials
See `/app/memory/test_credentials.md`.
