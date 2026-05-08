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

### Phase 5b — broker wire (4-gate defense in depth) + Promotion Diff UI

**Phase 5b broker wire** (`services/ml/broker_wire.py`):
  - Sits AFTER `RoadGuard.evaluate` in `shadow_wiring.py`
  - Computes `will_actually_fire` from FOUR gates ALL closed by default:
    - Gate 1 — `EQUITY_EXECUTOR_ENFORCE_ENABLED` / `CRYPTO_EXECUTOR_ENFORCE_ENABLED` (per lane)
    - Gate 2 — `BROKER_LIVE_ORDER_ENABLED` (global kill-switch)
    - Gate 3 — `RISEDUAL_LIVE_EXECUTION=1` (legacy opt-in)
    - Gate 4 — Calibration Kanban readiness (lane must be `Eligible`)
  - `_dispatch_to_broker` is a **NO-OP placeholder body** — even with all 4 gates open, no broker SDK is contacted (intentional safety)
  - Persists ONE `phase5b_intents` row per signal with classification `SHADOW_ONLY | GATE_BLOCK | WOULD_HAVE_FIRED | FIRED`
  - Admin endpoints: `/api/admin/ml/v2/phase5b/{summary, recent}`
  - 30-day TTL on `phase5b_intents`

**Promotion Diff UI**:
  - Inline expandable RG verdict log on each Kanban LaneCard
  - "Show last 50 RG verdicts" button → fetches from existing `/api/admin/ml/v2/roadguard/decisions/recent?lane=X&limit=50`
  - Sticky-header table with When / Symbol / Decision / Gate / Reason
  - Colour-coded decisions (PASS=emerald, REDUCE=amber, BLOCK=rose)
  - data-testids on every row for testability

### 8-ML stack (Phase 0–4.5) — rebuilt from scratch this session
[Previous architecture preserved — see git history for details]

### Closed-loop RoadGuard pair (Phase 4 — user spec)
  - `EquityRoadGuard` + `CryptoRoadGuard` — each pinned to its lane via G00 LANE_MISMATCH
  - Independent envs (`ROADGUARD_EQUITY_*` vs `ROADGUARD_CRYPTO_*`)
  - Lane-specific decision collections: `roadguard_equity_decisions`, `roadguard_crypto_decisions`
  - Zero cross-contamination — pinned by tests

### Phase 5a — receipt-only ML pipeline wiring
  - `services/ml/shadow_wiring.py` runs the full 8-ML pipeline + lane-RG + Phase 5b on every executor signal
  - Writes ONE `alpha_decision_log` receipt + ONE `roadguard_<lane>_decisions` row + ONE `phase5b_intents` row per signal
  - **NEVER calls a broker. NEVER places an order.**

### Calibration Kanban (read-only admin tile)
  - Per-lane Shadow → Calibrate → Enforce promotion ladder
  - 6-item checklist: min_receipts, zero_contamination, zero_false_blocks, broker_health_stable, correct_collection, enforce_off
  - States: Eligible / Ready for Review / Blocked
  - Buttons display state, NEVER flip enforcement

### Alpha helpers
  - `alpha_decision_log` (8-stage receipts, 30d TTL)
  - `alpha_weight_calibrator` (sizes from `min(equity, cash)`, NEVER buying_power; $500 cap / 5 slots)
  - `alpha_daily_mandate` (≥20 RT/day target, 0.70→0.60 pressure curve, 15:30 ET no-open, 15:55 force-flat)

### Tests
**Total: 218 backend pytest passing** (60 pipeline + 16 lane-pair + 11 kanban + 15 broker_wire + 13 alpha + 16 boundary + 11 perception + 76 existing)

Test files: `test_ml_boundary.py`, `test_ml_perception.py`, `test_ml_pipeline_v2.py`, `test_roadguard_pair.py`, `test_calibration_kanban.py`, `test_broker_wire.py`, `test_alpha_helpers.py`

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
