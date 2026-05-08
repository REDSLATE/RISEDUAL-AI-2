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

### Phase 5d guard rails — `make lint-arch` / `make lint-fast` + opt-in pre-push hook (2026-05-08)

**Goal**: protect the green-CI baseline as Phase 6 prep touches more files.

**`Makefile` targets** (`/app/Makefile`):
  - `make lint-arch` — direction-tuple drift + oversized files. 7 tests, ~1s.
  - `make lint-fast` — superset: lint-arch PLUS the Phase 6 invariant bundle (dual-stack, kill-switch, authority/risk-budget, RoadGuard pair, RoadGuard, fast-veto, executor lanes). 126 tests, ~1.5s total. Read-only, no Mongo writes, no broker calls. Stops at first failure via Make dependency chain.
  - `make help` documents both targets.

**Opt-in pre-push hook** (`/app/scripts/install-pre-push-hook.sh`):
  - `./scripts/install-pre-push-hook.sh` → installs `lint-arch` hook (default; quick drift check).
  - `./scripts/install-pre-push-hook.sh --fast` → installs `lint-fast` hook (broader Phase 6 safety check).
  - Hook embeds `target=...` marker so the operator can see which mode is active.
  - Re-install in either mode swaps targets idempotently. `--uninstall` removes only managed hooks.
  - Bypass once: `git push --no-verify`. **Not installed automatically.**

**Verified**:
  - `make lint-arch`: 7 passed in ~1s.
  - `make lint-fast`: 7 + 119 = 126 passed in ~1.5s.
  - Synthetic violation: detected at `lint-arch` step, dependency chain stops `lint-fast` early, exit non-zero.
  - Hook lifecycle: install (default) → install (--fast) → re-install plain → uninstall → uninstall-when-absent (safe no-op).
  - No allowlist additions, no behavior code changes.

### Phase 5d Wedge alerter — heartbeat-driven paging (2026-05-08)

**Notification-only** wedge alerter (`services/wedge_alerter.py`). NO promotion, NO enforcement, NO broker calls, NO scheduler restart, NO automatic remediation.

**Trigger rules**:
  - **T1** `any_frozen=true` for >`WEDGE_FROZEN_THRESHOLD_MIN` min (default 30) — tracked per-lane via `frozen_started_at`.
  - **T2** `top_clamp_block_reason == STRATEGIST_FEATURE_HEALTH_LOW` with `top_clamp_block_count > WEDGE_FEATURE_HEALTH_THRESHOLD` (default 50) in last 1h.

**Behavior**:
  - Posts ONE alert to `OPS_ALERT_WEBHOOK_URL` (Slack/Discord/generic-compatible JSON).
  - Cooldown: `WEDGE_ALERT_COOLDOWN_HOURS` (default 4h) suppresses duplicate `(lane, rule)` alerts.
  - Audit row written to `wedge_alerter_history` regardless of webhook success.
  - Missing webhook logs `OPS_ALERT_WEBHOOK_URL_MISSING` once per tick — never crashes.
  - Lane recovery clears the frozen-timer in `wedge_alerter_state.lane_frozen_started_at`.
  - Tier-3 firewall: only writes to `wedge_alerter_state` + `wedge_alerter_history`, NEVER to `alpha_decision_log` / `roadguard_*` / `phase5b_intents` / `paper_trades`.

**New scheduler job**: `wedge_alerter_tick` interval=5min — safe to schedule even without webhook (logs and returns).

**New admin endpoints** (`ml_safety_router`, all read-only):
  - `GET /api/admin/ml/wedge-alerter/status` — config flag + persisted state shape.
  - `GET /api/admin/ml/wedge-alerter/history?limit&lane` — audit rows.
  - `POST /api/admin/ml/wedge-alerter/run-now` — manual one-tick trigger.

**Tests**: `tests/test_wedge_alerter.py` (10 unit) + `tests/slow/test_wedge_alerter_api.py` (26 API). Combined Phase 5d suite: **79 passed** end-to-end. Hard stop on Phase 5b promotion remains in effect.

### Phase 5d cleanup — Tech-debt batch (2026-05-08)

**Direction-tuple cleanup**: replaced 5 literal direction tuples with the canonical `Verdict` enum / `canonical_ai_dir` helper. No allowlist additions.
  - `services/alpha_decision_log.py:131` → `(Verdict.BUY.value, Verdict.SELL.value)`
  - `services/ml/shadow_wiring.py:199, 248, 278` → `(Verdict.BUY.value, Verdict.SELL.value)`
  - `services/fast_veto_layer.py:286` → `canonical_ai_dir(council_action) != "UNKNOWN"`

**RoadGuardTile.jsx split** (was 525 lines, now 412): extracted 3 sub-components without behavior change.
  - `RoadGuardLaneCard.jsx` — per-lane summary card
  - `RoadGuardStatsRow.jsx` — `Metric` + `ScopeButton` primitives
  - `RoadGuardReasonBadge.jsx` — `DecisionBadge` + `ChecklistRow` primitives

**Tests**: `test_no_local_direction_tuples.py` (4/4) + `test_code_size.py` (3/3) now passing. Full pytest: **2755 passed, 0 failed**. UI smoke screenshot confirms RoadGuardTile renders identically.

### Phase 5d — Stale-model / Feature-health / Heartbeat safety patch (2026-05-08)

**Stale-Model Protection** (`services/ml/model_age.py`):
  - `evaluate_artifact(path)` returns `(stale, age_hours, max_age)`. Threshold via `MAX_MODEL_AGE_HOURS` env (default 72).
  - `ModelBootReceipt` extended with `model_age_hours: Optional[float]` and `stale: bool`.
  - When stale, Strategist/Auditor/Executor boot flips `ready=False` with reason `MODEL_STALE_OBSERVE_ONLY:age=Xh>max=72h`. Layer.decide() then returns NO_TRADE — no override flag exists.

**Feature-Health Weighting** (`services/ml/feature_health.py`):
  - `compute_feature_health(frame)` returns `(score, diagnostics)` with components: perception confidence (50%), market completeness (30%), data freshness (20%).
  - Strategist clamps `effective_confidence = raw_confidence * feature_health_score`.
  - Below `FEATURE_HEALTH_HOLD_THRESHOLD` (default 0.3) → forces NO_TRADE with reason `STRATEGIST_FEATURE_HEALTH_LOW`.
  - Reason flips to `STRATEGIST_CONFIRM_CLAMPED` / `STRATEGIST_FLIP_CLAMPED` when clamping is active.

**Executor Heartbeat** (`services/ml/executor_heartbeat.py`):
  - Thread-safe in-process tracker. Records every `pipeline.decide()` per lane.
  - Surfaces `last_pipeline_run_at`, `last_signal_at`, `signals_1h` (total runs), `buy_sell_1h`, `holds_1h`, `feature_health_avg`, `model_age_hours`, `model_stale`, `top_clamp_block_reason`, `top_clamp_block_count`, `frozen`.
  - Frozen heuristic: no run in `EXECUTOR_HEARTBEAT_FREEZE_AFTER_MIN` minutes (default 15) OR (buy_sell_1h=0 AND avg health < 0.3).

**ML Heartbeat tile** (`frontend/src/components/admin/MLHeartbeatTile.jsx`):
  - Top row of Calibration Kanban admin page. 30s auto-refresh + manual refresh button.
  - Shows lane / frozen / last_run / signals_1h / buy_sell_1h / holds_1h / health_avg / model_age / stale / top reason.
  - Strict read-only: no promotion buttons, no flip-enforcement controls, no broker-write controls.

**New admin endpoints** (`/api/admin/ml` prefix, separate `ml_safety_router`):
  - `GET /api/admin/ml/heartbeat` — per-lane state with frozen flag, model_stale flag.
  - `GET /api/admin/ml/pipeline/receipts?limit&lane` — recent decision-log entries.

**Tests**: `tests/test_phase5d_safety.py` (14 unit) + `tests/slow/test_phase5d_safety_api.py` (29 API). All 43 passing. **Hard stop on Phase 5b promotion remains in effect** — no broker wiring changes, no BUY/SELL enablement.

### Phase 5c — sklearn-backed Strategist + Auditor, live features, Camaro bridge

**Real Strategist + Auditor ML** (`services/ml/strategist/base.py`, `services/ml/auditor/base.py`):
  - `StrategistML` — `RandomForestClassifier`, 10-feature vector (perception scores + Shelly recall ratios + intent hint), 3-class output (BUY/SELL/NO_TRADE)
  - `AuditorML` — `RandomForestClassifier`, 8-feature vector, binary output (CONFIRM/DOWNGRADE — never flips direction)
  - Both artifact-gated via `STRATEGIST_ARTIFACT` / `AUDITOR_ARTIFACT` env vars
  - Falls back to balanced synthetic-trained placeholders when no artifact set
  - Boot receipts now show `placeholder_classifier` (was `placeholder_deterministic`)

**Live feature extraction** (`services/ml/feature_extraction.py`):
  - Pulls from existing services: `alpaca_equity_quotes`, `kraken_crypto_quotes`, `news_shock_service`, FRED snapshot via `fred_snapshots` Mongo collection
  - Wired into `shadow_wiring.run_shadow_pipeline` after `_build_feature_frame`
  - NEVER raises — every fetch is try/except, missing data falls into the safe middle of each model's training distribution
  - Caller-supplied `base` market dict wins over live data (synthetic dry-runs stable)

**Camaro → Shelly bridge** (`services/ml/camaro_shelly_bridge.py`):
  - Reads closed day-trade rows from `paper_trades` + `crypto_paper_trades` where `source="day_trade_scanner"` and `status="closed"`
  - Transforms each into a regime payload with `source="camaro"` (the canonical user-requested label)
  - Idempotent `prediction_id`: `camaro::<trade_id>::<closed_at>`
  - Calls `market_memory_service.save_regime` (Shelly's ChromaDB)
  - Audit trail in `camaro_shelly_bridge_log` collection
  - Admin endpoints: `POST /api/admin/ml/v2/camaro/bridge/run?lookback_hours=24`, `GET /api/admin/ml/v2/camaro/bridge/status?limit=10`

**Real .joblib artifacts** (`backend/scripts/train_ml_artifacts.py`):
  - Produces 8 artifacts under `/app/artifacts/ml/` (6 perception + Strategist + Auditor)
  - Wire into `.env` via `PERCEPTION_*_ARTIFACT` / `STRATEGIST_ARTIFACT` / `AUDITOR_ARTIFACT` paths to swap from in-process placeholders to disk-loaded models
  - Re-runs of the script use synthetic-realistic data; replace the training data builder when receipts mature into labeled data

**OPS_ALERT_WEBHOOK_URL**: wiring already correct in `services/ops_alerter.py`. Operator action: set `OPS_ALERT_WEBHOOK_URL=<slack-or-discord-webhook>` in prod `.env` and the alerter activates on next tick.

### Tests
**Total: 252 backend pytest passing** (208 + 13 phase5c features+camaro + 31 verified by testing agent for endpoints)

This iteration adds: `tests/test_phase5c_features_and_camaro.py`

## What's Implemented (continued)

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
