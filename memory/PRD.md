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

## What's Implemented (this fork — 2026-05-08 / 2026-05-09)

### Step 4B — `trading_bot_service.py` telemetry/receipts extraction (2026-05-09)

**Micro-phase 2 of 5**. Telemetry/receipts only — zero behaviour change, no broker calls, no decision logic, no DB query change.

`services/trading_bot_service.py`: 1554 → **1506 lines** (-48).

**Moved** to `services/trading_bot/_telemetry.py` (93 lines):
| Function | Authority |
|---|---|
| `fire_equity_shadow` | Research Shadow Layer fire-and-forget logger (kicks off `services.research_shadow_engines.fire_shadow` as a background task). Tier-3 firewall enforced by callee. |
| `record_kill_switch_outcome` | Step 8 of `execute_signal` — feeds broker outcome into the `ai_core.kill_switch` error window. Counts `{"error": ...}` dicts as failures. |

**Compatibility shim**: re-imported under the original private names (`_fire_equity_shadow`, `_record_kill_switch_outcome`) so the in-module call sites at lines 503/530 are unchanged. Deferred import of `_resolve_db_for_shadow` inside `fire_equity_shadow` breaks the circular dependency cleanly — the helper still routes through tbs's DB resolver.

**Verified**:
- `make lint-arch` 9 passed · `make lint-fast` 128 passed · `make lint-safety` 72 passed.
- Targeted suite (execute_signal + executor_lanes + roadguard + fast_veto + kill_switch + shadow + trading_bot + portfolio_risk + adaptive_sizing + drawdown): **393 passed**.
- Full pytest: **2824 passed, 0 failed** (identical pre/post extraction).
- No receipt/audit shape changes. No broker behaviour changes.

**Remaining**: 4C (data access), 4D (decision rules), 4E (broker calls — LAST).

### Step 4A — `trading_bot_service.py` constants extraction (2026-05-09)

**Micro-phase 1 of 5** (operator-mandated slice-then-test cadence). Pure constants/types only — zero behaviour change.

**Target file** (per operator decision): `services/trading_bot_service.py` (1575 → **1554 lines**, -21). The originally-named `day_trade_executor.py` doesn't exist; `trading_bot_service.py` is the real top-level bot orchestrator.

**New package** `services/trading_bot/`:
| File | Lines | Authority |
|---|---:|---|
| `__init__.py` | 15 | Step 4 plan + scope notes |
| `_constants.py` | 54 | 7 module-level constants — pure values, no behaviour |

**Constants moved**: `ADAPTIVE_SIZING_ENABLED`, `_MIN_SCALED_QTY`, `MAX_POSITION_USD`, `MAX_PORTFOLIO_EXPOSURE`, `MAX_CONCURRENT_TRADES`, `MAX_SECTOR_EXPOSURE_PCT`, `BOT_TYPES`.

**Compatibility shim**: `trading_bot_service.py` does `from services.trading_bot._constants import *names*` so:
- External attribute access `tbs.MAX_POSITION_USD` still resolves (the names live in `tbs.__dict__` after `from import`).
- The monkeypatch pattern in `test_trading_bot_adaptive_sizing.py` (`monkeypatch.setattr(tbs, "ADAPTIVE_SIZING_ENABLED", True)`) still mutates the binding bare-name reads inside `tbs` resolve through.

**Verified**:
- `make lint-arch` 9 passed · `make lint-fast` 128 passed · `make lint-safety` 72 passed.
- Targeted suite (`test_portfolio_risk_engine` + `test_trading_bot_adaptive_sizing` + `test_execute_signal_usd` + `test_executor_lanes` + `test_drawdown_allocator`): **120 passed**.
- Full pytest: **2824 passed, 0 failed** (identical pre/post extraction).
- Backend boots clean. External `tbs.X` attribute access programmatically asserted.

**Remaining micro-phases**: 4B (telemetry/receipts), 4C (data access), 4D (decision rules), 4E (broker calls — LAST).

### Step 3 — `_start_schedulers()` strangler split (2026-05-09)

**`server.py` shrinks from 2004 → 1537 lines (-467).** Job registration moved verbatim to `services/scheduling/jobs.py`. Zero behaviour change: every job ID, interval, cron expression, `replace_existing`, and `next_run_time` kwarg preserved. Live scheduler still registers **61 jobs** (57 static + 4 dynamic ETL).

**New package** `services/scheduling/`:
| File | Lines | Authority |
|---|---:|---|
| `__init__.py` | 15 | Re-exports `register_all` |
| `jobs.py` | 127 | Flat schedule manifest — pure `scheduler.add_job(...)` calls |
| `_callbacks.py` | 267 | 14 inline async callbacks + production-incident rationale |

**`server._start_schedulers` retains** (unchanged authority):
- AsyncIOScheduler instantiation
- ``scheduler.start()``
- self-test scheduler wiring (`set_scheduler(scheduler)`)
- Health-panel error pump (`set_scheduler_boot_error`)
- Auto-seed Tier3 universe block (startup data seed, NOT job registration)

**Design decision — `server_mod` injection + callback extraction**:
- `register_all(scheduler, db, server_mod)` accepts the live `server` module so it can reach module-level callbacks (`_check_smart_orders`, `_run_grid_bots`, `_run_etl_job`, ~40 others) without a circular import.
- 14 closures that previously lived inline (`_run_ticker_abandonment_snapshot`, `_run_kraken_shadow_compare`, `_write_scheduler_heartbeat`, etc.) were lifted out to `_callbacks.py` as top-level `async def fn(db)` functions and wired via APScheduler's `args=[db]` — same runtime behaviour, much tighter `jobs.py`.

**Verified**:
- Boot: Schedulers started — same 61 jobs (57 static + 4 dynamic ETL).
- `make lint-arch` 9 passed · `make lint-fast` 128 passed · `make lint-safety` 72 passed.
- `test_code_size.py`: 5/5 passed (jobs.py 127, _callbacks.py 267 — both well below core-governance preferred 600).
- Full pytest: **2824 passed, 0 failed** (identical pre/post split).
- Live `/api/admin/self-test`: scheduler check PASS, 61 jobs registered.

**Architecture Split Steps 1, 2, 3 now complete. Step 4 (split `day_trade_executor.py` ~1100 lines) remains.**

### Step 2 — `admin_ml_v2.py` authority-boundary split (2026-05-08)

**543-line route file → 7 small modules, largest 206 lines.** Zero behavior change. Compatibility shim preserves every existing import.

**New package** `routes/admin_ml/`:
| File | Lines | Authority |
|---|---:|---|
| `_routers.py` | 35 | APIRouter + DB handle (no endpoints) |
| `_auth.py` | 25 | Late-bound `require_admin` (test-monkeypatch compatible) |
| `__init__.py` | 60 | Side-effect imports + re-exports |
| `v2_pipeline.py` | 84 | Synthetic pipeline dry-run |
| `kanban.py` | 104 | RoadGuard pair + calibration kanban |
| `receipts.py` | 189 | Boot receipts + decision log + Phase 5b + Camaro |
| `safety.py` | 206 | Heartbeat + pipeline receipts + promotion checklist + artifacts + wedge alerter |
| **`admin_ml_v2.py`** | **62** | **Compatibility shim — re-exports public surface** |

**Compatibility preserved**:
- `route_registry.py`'s `from routes.admin_ml_v2 import (router, ml_safety_router, set_db, ...)` still works.
- Test sites that `monkeypatch.setattr(admin_ml_v2, "_require_admin", ...)` still work — `_auth.require_admin` resolves through `sys.modules["routes.admin_ml_v2"]` at every call.
- Direct test imports (`admin_ml_v2.list_artifacts_endpoint`, `admin_ml_v2.promotion_checklist`) re-exported.

**Verified**:
- 15 endpoint routes live-curl tested: every one returns 200 with auth, 401 without (same paths, same shapes).
- `lint-arch` 9 passed · `lint-fast` 119 passed · `lint-safety` 72 passed.
- Targeted suites: `test_artifact_inventory` + `test_promotion_checklist` + `test_phase5d_safety` + `test_wedge_alerter` + `test_code_size` = **60 passed**.
- Full pytest: **2824 passed, 0 failed** (same count as before split).
- `admin_ml_v2.py` removed from `PREFERRED_BASELINE` (62 ≤ 500 ceiling — ratchet worked exactly as designed).

### Architecture-as-policy — module-type aware code-size lint (2026-05-08)

**Two-tier policy** encoded in `tests/test_code_size.py`:

  - **Hard ceiling** (failing): unchanged — Python 800, frontend 500. Existing `ALLOWLIST` still works; no allowlist expansion.
  - **Preferred ceiling** (drift indicator): module-type aware, snapshot-based. Catches new code that violates authority-boundary expectations without breaking the green baseline.

**Module classification** by path predicate:

| Class | Preferred | Path |
|---|---:|---|
| `api-route` | 500 | `backend/routes/` |
| `ui-tile` | 400 | `frontend/src/components/admin/*Tile.jsx` |
| `ui-admin-component` | 500 | `frontend/src/components/admin/` |
| `ui-component` | 500 | `frontend/src/components/` |
| `core-governance` | 600 | `backend/services/` |
| `script` | 400 | `backend/scripts/` |
| `test` | 800 | `backend/tests/` |
| `default` | 800 | _fallback_ |

**Snapshot baseline** (`PREFERRED_BASELINE`): 59 existing breaches grandfathered with their line count + label. New files in those paths must respect the preferred ceiling. As old files shrink below their preferred ceiling, the lint fails with a "Remove from PREFERRED_BASELINE" message — ratcheting the bar tighter over time.

**Exempt patterns**: `migrations/`, `*_schema.py`, `*.schema.json`, `.egg-info/` — bypass both lints (no allowlist needed).

**Two new tests** added (lift count from 3 → 5):
  - `test_no_new_preferred_ceiling_breaches` — fails on new breaches OR stale baseline entries (file shrunk below ceiling, file moved/deleted).
  - `test_preferred_baseline_labels_match_classifier` — catches path drift (e.g. directory moves).

**Verified**:
  - All 5 tests pass (was 3); baseline cleanly captures today's breaches.
  - Synthetic drift smoke: new oversized api-route → fails with exact class+ceiling message; new oversized ui-tile → same; shrinking a baseline entry → "remove from baseline" message. Restoring → green.
  - `make lint-arch` 9 passed, `make lint-fast` 128 passed, `make lint-safety` 72 passed.
  - Full pytest: **2824 passed, 0 failed**.

**Set the bar for the upcoming Phase 6 prep splits**: `admin_ml_v2.py` (next), `_start_schedulers()`, `day_trade_executor.py` (last) — each must land within or move toward their preferred ceiling.

### Phase 5d/6 governance — `make lint-safety` (2026-05-08)

**Single-command verification of the entire Phase 5d/6 read-only safety surface**.

**Three-tier separation now established**:
  - `make lint-arch`   — architecture drift (~1s, 7 tests)
  - `make lint-fast`   — runtime invariants (~1.5s, 126 tests, includes lint-arch)
  - `make lint-safety` — Phase 5d/6 governance surface (~3s, 72 tests)

**`lint-safety` bundle**:
  - `test_phase5d_safety.py` — stale-model + feature-health + executor heartbeat
  - `test_wedge_alerter.py` — notification-only paging + cooldown
  - `test_artifact_inventory.py` — file-stat + env-read endpoint
  - `test_promotion_checklist.py` — 8-check aggregator
  - `test_retrain_alpha_models.py` — artifact-only dry-run scaffold

**Constraints honoured**: read-only, no broker calls, no env mutation, no joblib loading, no backend restart, no promotion actions. Stops at first failing file via `pytest -x`. On failure, prints the failing test node AND the full list of bundled layers so the operator immediately knows which surface regressed.

**Hook installer extended** with `--safety`:
  - `./scripts/install-pre-push-hook.sh --safety` → `make lint-safety` mode
  - Hook embeds `target=lint-safety` marker.
  - Re-install across all three modes (default / `--fast` / `--safety`) is idempotent.

**Intended uses**:
  - Pre-promotion verification.
  - Post-refactor sanity check.
  - Pre-deploy safety review.

**Verified**:
  - `make lint-safety`: 72 passed in 2.72s.
  - Synthetic failure smoke test: correctly stops after first failure, names the test node, lists all 5 bundled layers, exits non-zero.
  - All three speeds (`lint-arch` / `lint-fast` / `lint-safety`) work standalone and as hook installers.

### Phase 6 prep — Promotion Checklist endpoint + tile (2026-05-08)

**Read-only Phase 6 readiness aggregator**. Pulls from existing surfaces (artifact_inventory + executor_heartbeat + alpha_decision_log) and emits an 8-check status report with GREEN/YELLOW/RED.

**Module** (`/app/backend/services/ml/promotion_checklist.py`):
  - `build_checklist(db)` returns `{as_of, overall_status, ready_for_review, message, checks[8], active_artifacts, latest_artifacts, any_frozen, thresholds}`.
  - 8 checks: `manifest_present`, `latest_newer_than_active`, `active_on_disk`, `env_type_matches`, `heartbeat_not_frozen`, `no_flood_1h`, `active_age`, `shadow_receipts`.
  - **Severity**: RED wins overall → `ready_for_review=false`. YELLOW only → `ready_for_review=false` ("Items need review."). All GREEN → `ready_for_review=true` ("Ready for review.").
  - **Hard-rule invariants**: response NEVER contains "Promote now" / "promote_now" / "promote_action" / "set_active". No env mutation. No joblib load. No broker / executor / pipeline imports.

**Endpoint** (`/api/admin/ml/promotion-checklist` on `ml_safety_router`): admin-gated, GET-only.

**Frontend tile** (`MLPromotionChecklistTile.jsx`): rendered between Heartbeat and Artifacts on the Calibration Kanban.
  - 60s auto-refresh + manual refresh.
  - Per-row icon + status pill matching the colour scheme. Banner shows the overall message.
  - Footer surfaces active vs latest artifact filenames per type.
  - **Hard rule verified**: zero "Promote now" occurrences, zero promote/set-active/activate buttons in the tile.

**Tests**: `tests/test_promotion_checklist.py` (17 tests covering all 6 acceptance criteria + edge cases). Frontend testing_agent_v3_fork: 100% pass.

**Verified**:
  - 17/17 backend pytest pass.
  - Live smoke: 8 rows render (RED on heartbeat_not_frozen, GREEN on env_type_matches/no_flood/shadow_receipts=7, YELLOW on the rest because models dir + env unset). Banner reads "Blockers present — review the RED checks." / `overall=RED · ready_for_review=false`.
  - Full pytest: **2822 passed, 0 failed**.

### Phase 6 prep — ML Artifacts tile on Calibration Kanban (2026-05-08)

**Read-only Artifacts tile** (`frontend/src/components/admin/MLArtifactsTile.jsx`). UI-only surface; backend endpoint unchanged.

  - Renders below `MLHeartbeatTile` on the Calibration Kanban admin page.
  - `GET /api/admin/ml/artifacts` → sortable table (8 columns: Type, Filename, SHA, Timestamp, Size, Age, Manifest, Active).
  - **Default sort**: newest first by mtime. Click any column header to toggle/switch sort key.
  - **Visual cues** (read-only, no promotion power):
    * Green dot when `currently_pointed_to_by_env=true`.
    * Red `STALE` badge when active artifact has `age_hours > 72`.
    * Yellow `NO MANIFEST` badge when `manifest_present=false`.
    * Header banner `STALE ACTIVE ARTIFACT` when any active+stale row exists.
    * Cyan badge for `model_type=strategist`, violet for `auditor`, slate for `unknown`.
  - **Empty state**: "No model artifacts found." with the resolved `models_dir` path.
  - **Manual refresh button** + 60s auto-refresh interval.

**Hard-rule invariants** (verified):
  - NO promote button, NO set-active button, NO env-editing controls, NO write endpoints called, NO joblib loading.
  - All controls are read-only — only GET `/api/admin/ml/artifacts` is fetched.

**Verified**:
  - Backend: 14/14 pytest tests pass (`test_artifact_inventory.py`).
  - Frontend: testing_agent_v3_fork 100% pass — all UI elements render with proper `data-testid` attributes; sorting, refresh, badges, and banners all working; tile is read-only.
  - Live smoke (main agent): 4 seeded artifacts exercising all 4 cases (active+fresh+manifest, active+96h stale, inactive+no-manifest, unknown type) rendered correctly with green dot, red STALE badge, yellow NO MANIFEST badge, and `STALE ACTIVE ARTIFACT` header banner.
  - Test pollution cleaned up: empty `data/models/` and pristine `.env` after smoke test.

### Phase 6 prep — `GET /api/admin/ml/artifacts` (2026-05-08)

**Read-only artifact inventory**. File-stat + env-read only. Surfaces every `.joblib` under `/app/backend/data/models/` (override via `ALPHA_MODELS_DIR`).

**Module** (`/app/backend/services/ml/artifact_inventory.py`):
  - `list_artifacts(models_dir=...)` returns one dict per `.joblib`:
    `{filename, full_path, size_bytes, mtime, age_hours, sha_from_filename, timestamp_from_filename, model_type, manifest_present, manifest_path, currently_pointed_to_by_env, env_var}`.
  - Newest-first by mtime.
  - Recognises canonical retrain pattern `<type>_<sha>_<UTCstamp>.joblib`; legacy / non-canonical names tagged `model_type='unknown'`.
  - Flags `currently_pointed_to_by_env=true` only when `STRATEGIST_ARTIFACT` / `AUDITOR_ARTIFACT` env value's resolved path matches the file AND the model_type lines up. Cross-type mismatches and unknown rows always stay `false`.
  - **Module-level invariant**: never imports `joblib`, broker, executor, or pipeline modules — verified by source-level test.
  - Missing / unreadable directory returns `[]`, no crash.

**Endpoint** (`/api/admin/ml/artifacts` on `ml_safety_router`):
  - Admin-gated via `_require_admin`. Returns `{models_dir, items, count}`.
  - **Strict invariants**: no `joblib.load`, no env mutation, no broker/executor/pipeline calls, no promotion, no restart.

**Tests** (`/app/backend/tests/test_artifact_inventory.py`): 14 tests covering all 8 acceptance criteria — unauth blocked, admin 200, empty dir, strategist + auditor recognition, env-pointed flagging, no joblib load, no env mutation — plus cross-type-mismatch + unknown-type + ALPHA_MODELS_DIR override + source-level forbidden-import audit.

**Verified**:
  - 14/14 unit + 9 HTTP integration = **23/23 passing** via testing_agent_v3_fork.
  - Live curl: unauth → 401, admin → 200, empty `/data/models/` → `count: 0`, seeded artifact → correctly tagged.
  - Pre-existing pydantic `regex=` → `pattern=` deprecation warnings cleaned up at the same time (admin_ml_v2.py 5 sites).
  - Full pytest: **2796 passed, 0 failed**.

### Phase 6 prep — `scripts/retrain_alpha_models.py` (2026-05-08)

**Artifact-only retraining scaffold**. NO live promotion, NO env mutation, NO broker calls, NO executor wiring changes.

**Script** (`/app/backend/scripts/retrain_alpha_models.py`):
  - Reads `paper_trades` + `crypto_paper_trades` (outcome labels) + `alpha_decision_log` (feature reconstruction) within a configurable window.
  - Reconstructs the same 10-dim Strategist feature vector used at inference time (perception 6 sub-scores, avg confidence, shelly recall pos/neg ratios, intent encoded).
  - Strategist labels: realised P&L > 0 → BUY, < 0 → SELL, ≈ 0 → NO_TRADE. Auditor labels: losing trades → BLOCK.
  - Trains two `RandomForestClassifier` instances; reports `accuracy / precision_weighted / recall_weighted / ECE_10bin` plus class balance, train/test split, skip reasons.
  - Versioned outputs: `data/models/strategist_<git_sha>_<timestamp>.joblib` + `auditor_<...>.joblib` + `manifest_<...>.json` (source-tagged `alpha_retrain`).
  - Default mode is dry-run; `--write-artifact` persists files.
  - `--window-days N` (default 14), `--seed`, `--out-dir`, `--json /path` for machine-readable report.
  - Returns clean status `NOT_ENOUGH_ROWS` when `rows_used < 50`.
  - Returns clean status `NO_DB` when server db unimportable (no crash).

**Tests** (`/app/backend/tests/test_retrain_alpha_models.py`): 17 unit tests covering all 6 acceptance criteria — no rows → NOT_ENOUGH_ROWS, missing-features classification, versioned paths, dry-run no-write, stable schema, no env mutation — plus pure-helper coverage (label_from_outcome, class_balance, ECE, fit_classifier).

**Verified**:
  - 17/17 unit tests pass.
  - CLI smoke run against preview Mongo correctly returned NOT_ENOUGH_ROWS (172 trades scanned, 0 paired with decision logs in 1-day window).
  - testing_agent_v3_fork: 100% pass, zero issues.
  - Full pytest: **2782 passed, 0 failed**.

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
