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

## What's Implemented (this fork — 2026-05-08 / 2026-05-09 / 2026-05-10)

### Python Coach v0 — Alpha's Learning Surface (2026-05-10)

Operator-facing Python learning module. Turns a plain-English goal into a structured lesson plan via the existing `AIService` (Emergent Universal Key) and statically reviews pasted code via Python's `ast`. **Never executes user code.**

**Module layout** (`services/python_coach/`):
- `schemas.py` — Pydantic models for the API (`LessonPlan`, `LessonStep`, `CodeReview`, `CodeFinding`).
- `static_review.py` — pure AST reviewer. Detects SYNTAX errors, NO_FUNCTIONS, PRINT_ONLY, MISSING_DOCSTRINGS, NO_MAIN_GUARD, MIXED_INDENT, BARE_EXCEPT_PASS. Goal-aware drill suggester.
- `lesson_planner.py` — async `AIService` caller. Strict-JSON prompt + tolerant parser (strips fences, falls back to a stub on garbage). `generate_lesson_plan(goal)` and `generate_deep_feedback(code, goal)` — both NEVER raise.
- `api.py` — owner-only FastAPI router: `POST /plan`, `POST /review` (with optional `deep=True`), `GET /example`.

**Frontend**: `components/admin/PythonCoach.jsx` — new "Python Coach" tab in AdminPanel Insights. Goal textarea, code textarea, Stat tiles (Parses / Lines / Functions / Docstrings), Findings list with severity pills, optional LLM Deep feedback panel, "Load example" pulls the artifact's "fetch stock prices with retry" starter.

**Doctrine firewalls** (`tests/test_python_coach_v0.py`, **19 tests**):
- AST static review (6 unit tests).
- Forbidden imports: each coach module fails CI if it imports `services.code_evolution`, `services.broker`, `services.execution`, `subprocess`, `shlex`.
- Forbidden calls: each module fails CI if it contains `exec(`, `eval(`, `compile(`, `os.system(`, `subprocess.`.
- Bidirectional isolation: code_evolution may not reference "python_coach".
- Stub plan is valid `LessonPlan` even when LLM unreachable.
- All 3 endpoints invoke `_require_owner(request)` (static check).

**Live validation (2026-05-10)**:
- `POST /plan` for "learn list comprehensions" returned `generated_by: alpha-python-coach-llm` with 5 real drills + 4 pitfalls.
- `POST /review` with broken code → `parses=False`, single SYNTAX finding.
- `POST /review` with well-formed code → 0 findings.
- `GET /example` → starter snippet.
- 3180 → **3199 passing** (+19 new tests, no regressions). 583 routes.

### Code Evolution v0 — Self-Review Layer (2026-05-10)

RISEDUAL's code-review gate. Operator pastes a patch; the gate audits, classifies risk, recommends tests, and writes a Mongo receipt. **AI may not promote code, ever.**

**Doctrine** (literally enforced by tests):
```
def may_auto_promote(*args, **kwargs) -> bool:
    return False
```
Static source-grep + runtime invariants forbid any flip to `return True`.

**Module layout** (`services/code_evolution/`):
- `schemas.py` — dataclasses (internal) + Pydantic API models. ``PatchStatus`` literal: PROPOSED / BLOCKED_FORBIDDEN_PATTERN / BLOCKED_OPERATOR_ONLY / REQUIRES_DUAL_OPERATOR_SIGNATURE / REQUIRES_OPERATOR_SIGNATURE / SIGNED_AWAITING_OPS / REJECTED.
- `ast_invariants.py` — protected-path blocker (the gate cannot mutate itself), execution-path categoriser (CRITICAL paths require dual sig), risk-path categoriser (HIGH paths require single sig), forbidden-pattern regex (BROKER_LIVE_ORDER_ENABLED flips, COUNCIL_RISK_MODULATOR_ENABLED flips, delete_many, drop_collection, HOLD→BUY/SELL/LONG/SHORT mutations), AST-walk for destructive Mongo calls + direct inserts into protected collections.
- `code_auditor.py` — risk classifier (LOW/MEDIUM/HIGH/CRITICAL) + required-test recommender. Tests are recommended, never auto-run.
- `promotion_policy.py` — pure module (no Mongo, no FastAPI, no service imports). `may_auto_promote()`, `required_signatures_for(risk_level)`, `evaluate(invariants, audit) → PromotionPolicyResult`.
- `api.py` — FastAPI router, owner-only:
  - `POST /api/admin/code-evolution/evaluate` — full pipeline + Mongo upsert.
  - `POST /api/admin/code-evolution/countersign` — operator approve/reject. Each unique operator may sign once. BLOCKED_OPERATOR_ONLY can never be promoted via API (409).
  - `GET /api/admin/code-evolution/receipts` — paginated list (≤100), sorted by updated_at desc.
  - `GET /api/admin/code-evolution/receipts/{patch_id}` — single receipt.

**Mongo collection**: `code_evolution_receipts` (idempotent upsert by `patch_id`). Diff text is hashed (SHA-256) and never echoed back — only `diff_sha256` and `diff_size_bytes` persist.

**Hard invariants enforced by `tests/test_code_evolution_v0.py` (22 tests)**:
1. `may_auto_promote()` literally returns False (runtime + source check).
2. Patch touching `services/code_evolution/` → `BLOCKED_OPERATOR_ONLY`.
3. Patch with `BROKER_LIVE_ORDER_ENABLED=true` / `COUNCIL_RISK_MODULATOR_ENABLED=true` / HOLD→BUY → `BLOCKED_FORBIDDEN_PATTERN`.
4. AST walk catches `delete_many`, `drop_collection`, direct `paper_trades.insert_one` regardless of regex.
5. CRITICAL → 2 sigs, HIGH → 1, MEDIUM/LOW → 0.
6. Every promotion result reports `auto_promote=False`.
7. v0 ships ZERO subprocess imports — the test runner is explicitly future work.
8. `promotion_policy.py` stays pure (no fastapi / motor / routes / alpha_decision_log imports).

**Production validation (2026-05-10)**:
- Wired into `route_registry.py` — backend went from 575 → **579 routes**.
- Live smoke against 3 patch shapes:
  - Protected path → status `BLOCKED_OPERATOR_ONLY`, countersign returned **409** "cannot be promoted via the API".
  - Forbidden pattern → status `BLOCKED_FORBIDDEN_PATTERN`, sigs 0/2.
  - HIGH risk → 1 owner countersign flipped status to `SIGNED_AWAITING_OPS`, double-sign by same operator returned **409**.
- Receipts list endpoint sorts correctly, strips `_id`, surfaces sigs/required ratio.
- Test count: **3180 / 3180 passing** (3158 → 3180, +22 new code_evolution_v0 tests).

**Authority-boundary invariants preserved**:
- `BROKER_LIVE_ORDER_ENABLED=false`.
- No retrain artifact writes triggered.
- No threshold lowering anywhere.
- `trading_bot_service.py` Step 4E remains paused per operator order.
- Code Evolution gate cannot rewrite itself.

### Alpha Monorepo Sidecar (2026-05-09)

Wired the runtime to the RISEDUAL monorepo as a fire-and-forget observation sidecar. **NEVER blocks, NEVER raises** — local writes remain authoritative.

**Client (`services/risedual_monorepo_client.py`)**:
- `_enabled()` gate: requires `MONOREPO_BASE_URL`, `MONOREPO_INGEST_TOKEN`, `RUNTIME_NAME`. Honoured kill-switch `MONOREPO_SIDECAR_ENABLED=false`.
- `_post(path, body)`: 5s timeout via `httpx.AsyncClient`. Adds `runtime` to body, `X-Runtime-Token` header. Returns `{"ok": False, "error": ...}` on any failure — never raises.
- `fire_and_forget(coro)`: schedules on the running loop, drops cleanly when no loop is active, never raises.
- Public emits: `emit_receipt`, `emit_memory_label`, `register_calibrator`, `register_artifact`, `heartbeat`.
- Higher-level helper: `mirror_calibration_artifact(...)` — sha-hashes the joblib + fires both calibrator + artifact registration as one task.
- `aclose()` for shutdown cleanup.

**Wiring (single chokepoints)**:
- `services/alpha_decision_log.py::record_decision` — after the `insert_one` succeeds, schedules `emit_receipt(action="alpha_decision_log", intent=..., executed=False)`. Covers ALL six ADL paths automatically (crypto / day-trade / options / blocked-gate / equity paper / approved).
- `routes/admin_bulk_replay.py` — after labeling, emits one `emit_memory_label(...)` per upload (label = `quarantine` if any, else `review` if toxic, else `safe`).
- `services/calibration_layer.py::fit_and_persist` — after a successful joblib write, calls `mirror_calibration_artifact(...)`.

**Server lifecycle (`server.py`)**:
- Startup: schedules `_monorepo_register_artifacts_at_startup()` (one-shot register of the active calibrator) and `_monorepo_heartbeat_loop()` (60s cadence). Both are `asyncio.create_task` — they NEVER block boot.
- Shutdown: cancels heartbeat task + `aclose()` of the httpx client.

**Test net (`tests/test_risedual_monorepo_client.py` — 12 tests)**:
- `_enabled()` gate: kill-switch / missing env / present-env.
- `_post()` returns `sidecar_disabled` when off; swallows network errors silently.
- `fire_and_forget` no-loop drops the coro; on-loop schedules and runs.
- Body shape per emit (`receipts` / `memory-labels` / `calibrators` / `artifacts` / `heartbeat`).
- ADL wiring: `record_decision` mirrors a fake `emit_receipt` exactly once with the correct intent payload.

**Production status (2026-05-09)**:
- Backend boots cleanly: `[monorepo] sidecar enabled — heartbeat loop spawned`.
- Full pytest suite: **3151 / 3151 passing** (was 3139 — 12 new sidecar tests added).
- No regressions in calibration / bulk-replay / ADL receipt tests.
- `MONOREPO_BASE_URL`, `MONOREPO_INGEST_TOKEN`, `RUNTIME_NAME` already present in `.env`.

### FRED Macro Pipeline — Live (verified 2026-05-09)

`FRED_API_KEYS` is set in `/app/backend/.env`. Live verification:
- All 15 curated macro series populated (GDP, CPI, Core CPI, PCE, UNRATE, PAYEMS, ICSA, FEDFUNDS, DGS10, DGS2, T10Y2Y, HOUST, UMCSENT, BOPGSTB, GDPC1).
- `GET /api/fred/indicators` returns real values across 7 categories.
- No mock fallback in the macro feature pipeline.
- Search War Room FRED adapter (`adapters/fred.py`) reads the same env var via `KeyRotator`.

**Authority-boundary invariants preserved**:
- `BROKER_LIVE_ORDER_ENABLED` untouched (false).
- No retrain artifact writes triggered.
- No threshold lowering anywhere.
- The sidecar runs on the same process — failures are scoped to httpx timeouts and never re-raise.

### Confidence Calibration Layer — Option A (IsotonicRegression) (2026-05-09)

**Operator-approved Option A** with safe defaults: isotonic post-hoc mapping, fit only on firewall-trainable rows, served-confidence scope (CDO keeps raw), 24h refit cadence + manual admin trigger. Patent J thresholds unchanged. Execution authority unchanged.

**Backend** (`services/calibration_layer.py`):
- `fit_and_persist(rows)` — pulls rows through `chevelle_memory_labeler.trainable_only(...)`, drops UNRESOLVED / NEUTRAL / out-of-range, fits `sklearn.isotonic.IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip")`, persists to `models/calibrators/calibrator_<UTC-version>.joblib` + atomic `active.txt` pointer.
- `apply(raw)` — total: never raises. Returns `CalibrationApplyResult` with raw_confidence, calibrated_confidence, calibration_method=`"isotonic"`, calibration_model_version, calibration_sample_count, calibration_applied, fallback_reason. Falls back to raw with `calibration_applied=False` on: missing artifact / stale (>72h) / predict-exception / non-finite raw / out-of-range raw.
- `reliability_snapshot(rows)` — read-only Patent J payload: active_version, sample_count, ECE, Brier, 10-bin reliability table, apply_health flags.
- `MIN_SAMPLES=50` (env-tunable), `STALE_AFTER_HOURS=72` (env-tunable). Versioned artifacts kept on disk for audit.

**Wired into served confidence** (`services/ml_paper_trader.py:maybe_paper_trade`):
- `apply()` runs immediately before the ADL-6 receipt + `paper_trades.insert_one`.
- Calibration metadata appended to BOTH `trade_doc["calibration"]` AND the ADL receipt's signal payload — so retrain joins see both raw and calibrated values.
- `signal.confidence` (raw Platt-calibrated) UNCHANGED — execution gates and Patent J still fire against raw, exactly as the operator decreed.

**Admin endpoints** (`routes/governance_chevelle_calibration.py`, owner-only):
- `POST /api/governance/chevelle/calibration/refit` — pulls last 30d of paper_trades + crypto_paper_trades, runs the firewall, fits + persists.
- `GET  /api/governance/chevelle/calibration/status` — returns the Patent J card payload.
- `GET  /api/governance/chevelle/calibration/reliability` — convenience accessor.

**Daily scheduler** (`services/scheduling/jobs.py`):
- `chevelle_calibration_refit_daily` cron job at **04:15 UTC**. Failures swallowed and logged — next-day retry.

**Frontend** (`components/admin/PatentJCard.jsx`):
- New "Patent J" tab in the AdminPanel Insights group (sibling to Kanban, Bulk Replay).
- Health pills (Loaded / Fresh / version), 4 metric tiles (Samples / Rows seen / ECE / Brier), reliability bin table with drift colour-coding (green <5pp, amber <15pp, rose >15pp), "Refit now" button.
- Footer copy explicitly states observation-only nature and the fall-back contract.

**Production validation (2026-05-09 08:05 UTC)**:
- First refit on real data: **1376 firewall-trainable samples** out of 1474 rows pulled (98 rows quarantined by the labeler, exactly as designed).
- **ECE = 0.2461** — predictor is 24.6pp overconfident on average.
- Biggest bin (1363 samples at predicted 0.752) has realised win rate of 0.504 — isotonic compresses down to ~0.504 for that range. This is the kind of correction the user mentioned ("isotonic ... directly attacks the ECE/Brier issue without weakening J").
- Active calibrator persisted: `calibrator_2026-05-09T08-05-03Z.joblib`.

**Test net** (`tests/test_calibration_layer.py`, **24 tests**):
- Pure helpers: reliability_bins / ECE / Brier on synthetic perfectly-calibrated and inverted inputs.
- Firewall integration: quarantined rows excluded, UNRESOLVED outcomes excluded, out-of-range confidence dropped.
- Fit refused below MIN_SAMPLES (no artifact, no active pointer touched).
- Fit succeeds at 60 samples; metrics include pre/post ECE + Brier; post_ece ≤ pre_ece (isotonic Pareto invariant).
- Apply: fallback when no calibrator → applied=False, calibrated mirrors raw.
- Apply: returns calibrated after fit, version + sample_count populated.
- Apply: clips out-of-range and non-finite raw inputs with explicit fallback_reason tags.
- Apply: stale calibrator (>STALE_AFTER_HOURS) → fallback.
- Apply: predict() exception → fallback (`apply_exception`).
- Reliability snapshot: zero-state + populated state, JSON-serializable (no numpy leaks).
- Static authority firewall: NO Mongo writes, NO broker / executor / Strategist / Auditor / kill-switch imports, NO verdict emission.

**Verified**:
- `tests/test_calibration_layer.py`: **24 passed** in 0.98s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **3139 passed, 0 failed** (was 3115 + 24 new = 3139 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":575}` (572 → 575, +3 calibration endpoints).
- Live admin refit: 1376 samples, ECE 0.2461, calibrator artifact persisted.
- Frontend lint clean.

**What's NOT in this drop (per operator spec — held for later)**:
- Option B (temperature scaling) — not added until isotonic results are measured in production.
- `TRAIN_REAL_MIN` lowering — not touched.
- Patent J threshold lowering — not touched.
- v2 artifact promotion — not touched.

### ADL-6 — APPROVED success-path receipts in `ml_paper_trader` (2026-05-09)

**Closes Gap A surfaced by the 2026-05-09 diagnostic.** The day-of-deploy diagnostic showed `paper_trades` had 152 rows in 24h but `alpha_decision_log` had only 1 equity row, all `NO_TRADE` — meaning every APPROVED equity paper trade was orphaned from the ADL stream. v2 retrain join was structurally blind to APPROVED outcomes regardless of how long we waited.

**Root cause**: `services/ml_paper_trader.py:maybe_paper_trade` writes `paper_trades` rows but never called `schedule_shadow_receipt`. ADL-1 baseline at `trading_bot_service.execute_signal` only fires for the legacy webhook path; the ML orchestrator path (which produces ~all real equity paper trades) goes through `ml_paper_trader.maybe_paper_trade` directly.

**Patch site**: `services/ml_paper_trader.py:maybe_paper_trade` — single fire-and-forget `schedule_shadow_receipt` call IMMEDIATELY before `db["paper_trades"].insert_one(trade_doc)` so the receipt always lands first in the ADL stream (or fires even when the insert is a duplicate-key no-op).

**Call shape**:
```python
schedule_shadow_receipt(
    db,
    signal={
        "symbol": ticker,
        "direction": direction_val,
        "confidence": float(signal.confidence),
        "prediction_id": signal.prediction_id,
        "regime": regime,
        "trade_id": trade_id,
        "source_layer": "ml_paper_trader",
    },
    market_data=None,
    lane="equity",
    requested_notional_usd=float(position_usd or 0.0),
    source="ml_paper_trader",
)
```
Wrapped in defensive belt+braces try/except — debug log on failure, never blocks the trade write.

**Hard rails (pinned by tests)**:
- 6-test net `tests/test_ml_paper_trader_adl_receipts.py` covers: receipt call IMMEDIATELY precedes the `paper_trades.insert_one` (within 80 lines, same try block), `lane="equity"` and `source="ml_paper_trader"` static check, defensive try/except wrapper static check, no broker imports added, signal payload contains all required keys (symbol/direction/confidence/prediction_id/regime/trade_id/source_layer), helper failure does NOT propagate.
- All execution paths byte-equivalent: trade_doc shape, idempotency (duplicate-key handling), reasoning overlay, brake/sovereign/penalty meta — preserved.
- Adjacent suites still pass: `test_ml_paper_trader_idempotency` 6/6, all 5 prior ADL suites (63 tests total).

**Verified**:
- `tests/test_ml_paper_trader_adl_receipts.py`: **6 passed** in 0.11s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **3115 passed, 0 failed** (was 3109 + 6 new = 3115 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":572}`.

**Cumulative ADL coverage now spans SIX entry points**:
1. **Equity paper trades** (APPROVED success) — `ml_paper_trader.maybe_paper_trade` (**ADL-6 — closes the APPROVED gap**).
2. **Equity executor** (legacy webhook success) — `trading_bot_service.execute_signal` (ADL-0 baseline).
3. **Crypto paper bot** — `crypto_paper_trader.run_crypto_symbol` (ADL-2).
4. **Day-trade scanner** — `day_trade_scanner.run_scan` per-candidate (ADL-3).
5. **Options paper agent** — `trading_agents.options_paper.run` per-ticker (ADL-4).
6. **Upstream gate-blocked decisions** — six sites in `trading_bot_service` (ADL-5).

**Expected impact at May 13 checkpoint**:
- Per-lane equity coverage: 0.66% → ~100% (every paper_trade row gets a receipt).
- by_decision: NO_TRADE-only → mix of APPROVED + NO_TRADE.
- Criterion #4 (APPROVED + blocked both present): ❌ → ✅.
- Criterion #1 (receipt-vs-trade > 90%): equity will lift dramatically; crypto stays at ~63% naturally because `crypto_paper_trader.run_crypto_symbol` only fires receipts when there's a strategist signal (most loop iterations have no signal), so the gap is real-but-expected for the crypto lane.

**What ADL-6 does NOT fix** (still organic-window dependent):
- Gap B (day-trade + options 0 receipts) — ADL-3/4 hooks are correct; the agents just haven't produced signals yet in the 7h post-deploy window.
- Gap C (history pre-dating ADL persistence) — heals automatically over the next 30d.

### Bulk Replay — pre-ingest CSV firewall scanner (2026-05-09)

**Pre-ingest sanity check** for historical paper-trade CSVs. Operator drag-drops a file, every row passes through the existing Chevelle Memory Labeling Firewall, response includes per-row verdicts + aggregate summary. **NO** writes, **NO** training, **NO** promotion, **NO** broker calls — read-only by construction.

**Backend** (`routes/admin_bulk_replay.py`):
- `POST /api/admin/bulk-replay/scan` (owner-only) — multipart upload, returns:
  - `aggregate`: total_rows, trainable_count, quarantined_count, toxic_count, avg_trust_weight, by_source, by_lane, by_event_era, by_failure_mode, by_data_quality.
  - `rows`: per-row `{csv_row_index, symbol, opened_at, closed_at, source, lane, event_era, failure_mode, data_quality, trust_weight, trainable, rejection_reason, rule_trace, grade}`.
  - `parse_errors`: row-level CSV parse failures kept SEPARATE from labeler quarantines so the operator can distinguish "bad CSV" from "bad data semantics".
  - `warnings`: high-quarantine and high-toxic rate flags.
- **Caps**: `MAX_ROWS_PER_UPLOAD=500` (cap-overflow rows reported via `parse_errors`), `MAX_FILE_BYTES=2 MiB` (over-cap → 413).
- **Grade tags**: `GREEN` (trainable, no failure mode), `AMBER` (toxic — trainable at trust=0.10), `RED` (quarantined, trust=0.0).

**Frontend** (`components/admin/BulkReplayPanel.jsx`):
- Drag-drop file zone + "Choose a file" fallback, "Scan" button.
- Five aggregate stat tiles (Total / Trainable / Toxic / Quarantined / Avg Trust).
- Five breakdown tables (source / lane / event_era / failure_mode / data_quality).
- Filter chips (All / Green / Amber / Red).
- Per-row table with color-coded grade badges.
- Footer note explicitly states the read-only nature.
- Wired into `AdminPanel` → Insights group as "Bulk Replay" tab.

**Test net** (`tests/test_admin_bulk_replay.py`, **20 tests**):
- Auth: non-owner gets 403.
- Clean CSV → all GREEN / trainable.
- Toxic rows → AMBER, trust=0.10, still trainable (rule 7).
- Inferred toxic_high_confidence on 0.92-conf 60% loss with no explicit failure tag.
- Missing source → RED, rejection_reason=`missing_source`.
- Missing timestamps → RED, rejection_reason=`missing_timestamps`.
- Missing symbol → RED, rejection_reason=`missing_symbol`.
- Aggregate breakdowns present (all 5 dimensions).
- avg_trust_weight computed correctly.
- Empty CSV / header-only CSV → 200 with zero rows.
- Partial malformed row mixed with good rows → endpoint stays 200, bad row quarantines.
- Invalid UTF-8 bytes handled gracefully (decoder uses `errors="replace"`).
- Row cap enforced (600 rows uploaded → 500 scanned + cap-overflow error reported).
- File-size cap enforced (>2 MiB → 413).
- Response includes caps for operator visibility.
- Per-row payload has all required fields.
- Static authority firewall: NO DB writes, NO broker/executor/training imports, NO `import_into_memory`/`train_all`/`force_train` route definitions.

**Verified**:
- 20 new tests pass. Full pytest **3109 passed, 0 failed** (was 3089 + 20 new = 3109 ✓).
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86. Frontend lint clean.
- E2E live test: 4-row CSV with 1 clean / 1 toxic / 1 missing-source / 1 yfinance returned exact expected verdicts (GREEN 0.50 / AMBER 0.10 / RED 0.00 / GREEN 0.05) and breakdowns.
- Maintenance page → Owner sign-in → admin dashboard renders normally with the new "Bulk Replay" tab visible in the Insights group.

### Public-Access Lockout — "Technical Difficulties" mode (2026-05-09)

**Operator decree**: while the ML stack is in its organic data-collection
window (May 8 → ~May 22, 2026), the public site shows a "Technical
Difficulties" page. Owner / admin can still sign in and reach the
dashboard.

**Backend**:
- `routes/system_access.py` — public `GET /api/system/access` returns lockout state + whether caller is admin; owner-only `POST /api/system/access` flips at runtime (no redeploy).
- `services/public_access_middleware.py` — Starlette middleware that returns 503 with stable JSON `{"error":"service_unavailable","reason":"scheduled_maintenance","message":"..."}` for non-admin `/api/*` traffic when locked. **Always-reachable**: `/api/health`, `/api/auth/*`, `/api/system/access`, all non-`/api/*` paths (so the SPA can render its own lockout page).
- Admin bypass: JWT email matched against `ADMIN_EMAILS` env allowlist (default `admin@risedual.ai`). Only access tokens (`type=access`) count — refresh tokens do NOT bypass.
- **Fail-open** on internal errors (DB hiccup → middleware does NOT hard-lock the site).
- State precedence: Mongo `system_settings.public_access` runtime override → `PUBLIC_ACCESS_ENABLED` env flag (default `true`).

**Frontend**:
- `hooks/useSystemAccess.js` — polls `/api/system/access` on mount + every 30s. Exposes `{ ready, publicAccess, isAdmin, message, refresh }`.
- `components/MaintenancePage.jsx` — full-screen "Technical Difficulties" view with RISEDUAL branding, pulsing status dot, your operator copy, and "Owner sign-in" entry point that opens `AuthModal`.
- `App.js` gate — when locked AND not admin AND no signed-in user, renders `MaintenancePage` instead of the normal SPA. Re-polls access whenever auth state changes (admin login → lockout disappears without page reload).

**Tests** (27 new, `tests/test_public_access_middleware.py`):
- Bypass paths reachable while locked (`/api/health`, `/api/auth/*`, `/api/system/access`).
- Anonymous `/api/*` traffic gets 503 with stable JSON shape (`error`, `reason`, `message` keys).
- Frontend assets (non-`/api/*`) pass through under lockout.
- Admin via cookie OR `Authorization: Bearer` header bypasses.
- Non-admin email JWT does NOT bypass (even if perfectly valid).
- Refresh tokens do NOT bypass — only access tokens carry admin claim.
- Invalid / garbage JWT → no bypass.
- Resolver exception → fail open.
- Env-default truthy/falsy token table.
- ADMIN_EMAILS allowlist parses comma-separated, lowercases, strips whitespace.

**Verified**:
- 27 new tests pass. Full pytest **3089 passed, 0 failed** (was 3062 + 27 new = 3089 ✓).
- E2E smoke: anonymous `/api/dashboard` → 503; admin `/api/dashboard` → bypasses; `/api/health` always 200; `/api/auth/login` always reachable.
- Maintenance page screenshot renders cleanly with branding intact.

**Operator runbook**:
- **Engage lockout**: `POST /api/system/access {"enabled": false}` (auth as admin first).
- **Disengage lockout**: `POST /api/system/access {"enabled": true}`.
- **Inspect state** (no auth): `GET /api/system/access`.
- **Add additional admins**: set `ADMIN_EMAILS=admin@risedual.ai,other@example.com` in `backend/.env` and restart backend.

**Current state at session end**: lockout is **ON** per operator decree. Public sees the maintenance page; admin signs in via "Owner sign-in" link at the bottom of the page.

### Chevelle Memory Labeler — read-side firewall (2026-05-09)

**Pre-Chevelle infrastructure**: a pure label-resolution layer that
gates every memory before it can flow into Chevelle's training /
observation engine.

**Module layout** (3-file split, all under `core-governance` 600-line preferred ceiling):
| File | Lines | Authority |
|---|---:|---|
| `services/chevelle_memory_labels.py` | 111 | enums + trust-ladder constants + observation-policy tokens (data only) |
| `services/_chevelle_resolvers.py` | 324 | pure label-resolver helpers (era / lane / source / outcome / failure / quality / trust) |
| `services/chevelle_memory_labeler.py` | 376 | dataclass + public API (`label_memory`, `label_memories`, `trainable_only`, `quarantined_only`) |

**Observation policy** (added 2026-05-09 enhancement):
The `chevelle_can_observe` flag now responds to an optional
`observation_policy` parameter on `label_memory` / `label_memories`:
| Policy | Effect on `chevelle_can_observe` |
|---|---|
| `"all"` (default) | True for every well-formed row; False only for non-dict / None input |
| `"exclude_synthetic"` | False for synthetic-tier rows (`trust=0.05`) — useful during live drills |
| `"live_only"` | False for anything below `live_real_fill` (`trust=1.00`) — useful for ground-truth calibration passes |

Critical invariant pinned by tests: the policy gates **observation only**. `trust_weight` and `trainable` are invariant across policies — operator can flip policies between runs without re-labeling the corpus. Unknown policy strings fall back to `"all"` (never raise).

**The 10 operator-mandated hard rules** — pinned by tests:
1. Every memory must have `source` (or be quarantined).
2. Every memory must have `opened_at`/`closed_at` (or be quarantined).
3. Every memory must have a `lane` (`equity`/`crypto`/`options`/`macro`/`unknown`).
4. Every memory must have a normalized `symbol` (or be quarantined).
5. Old trades get an `event_era` (9-bucket: GFC_2008 / FLASH_CRASH_2010 / CHINA_DEVAL_2015 / VOL_SPIKE_2018 / COVID_2020 / RATE_HIKE_2022 / AI_BUBBLE_2024_2025 / CURRENT_REGIME / UNKNOWN_ERA).
6. Bad/ambiguous rows are `QUARANTINED` (trust=0.0, trainable=False), NEVER deleted.
7. Toxic/failure labels REDUCE trust (0.10 floor), never delete.
8. Chevelle MAY observe quarantined memories (`chevelle_can_observe=True`) but `trainable_only(...)` filters them out.
9. NO BUY/SELL verdicts emitted from labels (static check pinned).
10. NO broker/executor/Strategist/RoadGuard/FastVeto/kill-switch imports (static check pinned).

**Trust ladder** (operator-specified values, encoded as module-level constants):
| Source / state | Weight |
|---|---:|
| `live_real_fill` (alpaca / kraken / webull / live) | 1.00 |
| `recent_paper_trade` (≤30 days) | 0.50 |
| `historical_paper_trade` (>30 days) | 0.25 |
| `current_macro_proxy` (lane=macro / fred) | 0.15 |
| `toxic_memory` (BLOWUP / TOXIC_HIGH_CONFIDENCE / REGIME_MISMATCH / DATA_INTEGRITY) | 0.10 |
| `synthetic_backtest` (yfinance / pre-cutover) | 0.05 |
| `quarantined` (missing required fields) | 0.00 |

**Failure-mode taxonomy** (coarse-grained projection of the live
`services.failure_mode_classifier` taxonomy, suitable for training
gates): `NONE` / `BLOWUP` / `REGIME_MISMATCH` /
`TOXIC_HIGH_CONFIDENCE` / `DATA_INTEGRITY` / `UNKNOWN`.

**Test net** (`tests/test_chevelle_memory_labeler.py`, **56 tests**):
- All 10 hard rules covered (1-4 rejection paths, 5 era-mapping, 6 quarantine bucket, 7 toxic-not-deleted, 8 trainable filter, 9 no-verdict static check, 10 no-broker-import static check across all 3 split files).
- Trust ladder pinned per-tier.
- Symbol normalization (`BTC-USD` → `BTC`, `BTC/USDT` → `BTC`, etc.).
- Era mapping for all 9 buckets including pre-2008 → UNKNOWN.
- Idempotence + non-mutation of input dict.
- `non_dict_input` / `None` input quarantined (never raises).
- Recent vs. historical paper-trade boundary at 30 days.
- High-conviction loss → toxic_high_confidence inferred when no explicit failure marker.
- DB-write static check (no `.insert_*` / `.update_*` / `.delete_*` / `.bulk_write` / `.drop` anywhere in the 3 files).

**NOT integrated yet** — this is the labeler (the contract). Chevelle's training/observation engine itself still has to be built, and it MUST consume only `trainable_only(label_memories(...))`. No live call sites yet — by design, until Chevelle is ready.

**Verified**:
- `tests/test_chevelle_memory_labeler.py`: **56 passed** in 0.16s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **3054 passed, 0 failed** (was 2998 + 56 new = 3054 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":569}`.

### ADL-5 — upstream blocked-decision receipts in `trading_bot_service` (2026-05-09)

**Final micro-phase of the operator-mandated ADL persistence-coverage fix.** Captures gate-blocked decisions BEFORE they short-circuit the executor — the original ADL-1 success-path receipt only fired AFTER all gates passed, leaving every kill-switch / drawdown / fast-veto / sizing / RoadGuard ENFORCE block invisible to retraining.

**New helper** `services/trading_bot_service.py:_schedule_blocked_receipt(...)` (50 lines):
- Module-level fire-and-forget wrapper around `schedule_shadow_receipt`.
- Tags signal payload with `blocked_at` (gate stage) + `block_reason` (verbatim skip reason) on a COPY of the caller's dict — never mutates.
- Source tag: `"execute_signal_blocked"` (vs `"execute_signal"` for the success-path receipt) so retrain joins can disaggregate at the source level.
- Defensive belt+braces try/except — a raising or False-returning helper can NEVER alter `execute_signal`'s return value.

**Hooked at six gate sites in `trading_bot_service.py`**:
| Gate stage tag | Site | Coverage |
|---|---|---|
| `kill_switch_or_drawdown` | `_check_kill_switch_and_drawdown` skip path | kill switch active, kill switch trip on drawdown |
| `fast_veto_enforce` | Fast Veto enforce-mode path | shadow-promoted veto enforcement |
| `size_chain` | `_compute_adjusted_size` skip path | sector_cap / max_concurrent_trades / max_portfolio_exposure (all map to "portfolio limits reached"), drawdown allocator ("risk control"), low-confidence floor |
| `resolve_qty` | `_resolve_qty` skip path | invalid price / quote unavailable |
| `roadguard_enforce` | RoadGuard enforce-mode path | shared cross-lane caps, broker health, daily loss limit |
| `max_trades_per_day` | `process_signal_for_bots` dispatcher cap (line ~885) AND `process_webhook` daily cap (line ~1260) | daily trade rate limit at both entry points |

**Hard rails (pinned by tests)**:
- 12-test net `tests/test_execute_signal_adl_blocked_receipts.py` covers: each gate produces ONE receipt with correct `blocked_at`/`block_reason`/`lane`/`source`, caller's signal dict is NEVER mutated (deep-copy assertion), helper failure preserves the return shape, success path still produces ONLY the existing single `source="execute_signal"` receipt with no `blocked_at` key, kill-switch trip prevents downstream gates from running (no double-log), broker is NEVER called on a blocked path (tripwire on `_execute_bot_trade`), helper unit tests pin source tag + non-mutation + exception swallowing, static check that the helper uses the shared dispatcher and never invokes `run_shadow_pipeline` directly.
- All execution paths byte-equivalent: every skip dict, every error dict, every order shape, every loop's `continue` semantics — preserved.
- Code-size: `trading_bot_service.py` 1336 → 1490 lines (+154, well under existing 1575 baseline; allowlisted with documented justification).

**Verified**:
- `tests/test_execute_signal_adl_blocked_receipts.py`: **12 passed** in 0.19s.
- Targeted superset (test_execute_signal_usd + test_trading_bot_broker_contract + test_trading_bot_adaptive_sizing + test_executor_lanes + test_portfolio_risk_engine + test_execute_signal_adl_blocked_receipts + test_receipt_dispatch): **114 passed** in 1.42s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2998 passed, 0 failed** (was 2986 + 12 new = 2998 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":569}`.

**Cumulative ADL coverage now spans FIVE entry points across SIX gate stages**:
1. **Equity executor** — `trading_bot_service.execute_signal` success path (ADL-0 baseline).
2. **Crypto paper bot** — `crypto_paper_trader.run_crypto_symbol` (ADL-2).
3. **Day-trade scanner** — `day_trade_scanner.run_scan` per-candidate (ADL-3).
4. **Options paper agent** — `trading_agents.options_paper.run` per-ticker (ADL-4).
5. **Upstream gate-blocked decisions** — six sites in `trading_bot_service` (ADL-5): kill_switch_or_drawdown, fast_veto_enforce, size_chain (sector_cap / max_concurrent / max_portfolio / risk_control / low_confidence), resolve_qty, roadguard_enforce, max_trades_per_day (dispatcher + webhook).

**ADL persistence-coverage fix is COMPLETE.** Operator decree: let receipts accumulate organically for 24–48h, then re-run `python -m scripts.diagnose_alpha_decision_log_persistence` and `python -m scripts.diagnose_alpha_retrain_join` to confirm:
- Per-lane receipt-vs-trade ratios > 90%.
- Receipt distribution across `blocked_at` stages.
- Retrain-join coverage rises from 2.50% → ≥ 50% in the next 30-day window.

**ADL Coverage Tile** remains held until that organic window passes.

### ADL-4 — wire `options_paper` agent to the shared receipt helper (2026-05-09)

**Micro-phase 4 of 5** of the operator-mandated ADL persistence-coverage fix. Closes the options-lane gap.

**Survey findings**:
- Real options paper executor exists at `services/trading_agents/options_paper.py:run` (160 → 196 lines after wiring).
- Scheduled every 30 min via APScheduler job `agent_options_paper`.
- Trades execute via `record_paper_trade` → `learning_engine_trades` Mongo collection (tagged `strategy="options_paper"`).
- Per-ticker decision object `(direction, confidence)` is built by `_score_ticker(ticker, db)` reading from `features_snapshots`.
- Decision points: APPROVED (conf ≥ 0.72 → trade), NO_TRADE (conf < 0.72 → skip), post-signal skipped (price<=0, record_paper_trade returns falsy).
- Pre-signal early returns (deliberately NOT logged): db None, kill switch active, `_score_ticker` returns None.

**Patch site**: insertion point right AFTER `direction, conf = scored` unpack and BEFORE the conviction floor check. One call covers APPROVED + NO_TRADE uniformly.

**Call shape**:
```python
schedule_shadow_receipt(
    db,
    signal={
        "symbol": ticker,
        "direction": direction,
        "confidence": float(conf),
        "strategy": _STRATEGY,                # "options_paper"
        "min_confidence_threshold": _MIN_CONFIDENCE,  # 0.72
        "watchlist_position": considered,
        "source_layer": "options_paper_agent",
    },
    market_data=None,
    lane="options",
    requested_notional_usd=float(_POSITION_USD),  # 500.0
    source="options_paper_bot",
)
```
Wrapped in defensive belt+braces `try/except` that logs `[options_paper] ADL receipt schedule skipped` at DEBUG.

**Hard rails (pinned by tests)**:
- 13-test net `tests/test_options_paper_adl_receipts.py` covers: APPROVED (conf≥0.72) schedules receipt with options lane/source, NO_TRADE (conf<0.72) STILL schedules receipt, signal payload integrity (symbol/direction/confidence/strategy/source_layer), pass-through of `_POSITION_USD` as `requested_notional_usd`, three pre-signal early returns produce ZERO receipts (db None, kill switch ON, `_score_ticker` returns None), raising helper does NOT crash `run`, helper return=`False` does NOT branch caller, plus two static checks (no `run_shadow_pipeline` direct call, no broker/executor imports).
- Options paper agent execution path is byte-equivalent: kill-switch behaviour, conviction gate, MAX_OPENS limit, watchlist iteration, narrate_scan_skip behaviour — all unchanged.
- Code-size: `options_paper.py` 160 → 196 lines (+36, well under `core-governance` 600 preferred / 800 hard ceiling).

**Verified**:
- `tests/test_options_paper_adl_receipts.py`: **13 passed** in 0.15s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2986 passed, 0 failed** (was 2973 + 13 new = 2986 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":569}`.

**Cumulative ADL coverage now spans FOUR lanes**: equity executor, crypto paper bot, day-trade scanner, options paper agent. All four hooked via the same `schedule_shadow_receipt` helper for uniform fire-and-forget semantics.

**Remaining ADL micro-phase**:
- **ADL-5**: Move/duplicate receipt earlier so kill_switch / risk_guard / sector_cap / max_trades_per_day / RoadGuard ENFORCE blocked decisions also produce receipts.

### ADL-3 — wire `day_trade_scanner.py` to the shared receipt helper (2026-05-09)

**Micro-phase 3 of 5** of the operator-mandated ADL persistence-coverage fix. Closes the day-trade scanner gap — every scanned candidate now produces an `alpha_decision_log` receipt regardless of whether the gates approve or block it.

**Patch site**: `services/day_trade_scanner.py:run_scan` per-candidate loop. Insertion point is INSIDE the iteration, AFTER `apply_gates` resolves the gate verdict (passed/blocker/inputs) and AFTER the `gate_passed` / `gate_blocker` / `chosen` book-keeping. The original `if not passed: ... continue` was restructured to an `if/else` so both branches fall through to a single shared receipt-scheduling block — APPROVED candidates AND blocked candidates both get one receipt per scan iteration.

**Call shape** (verbatim):
```python
schedule_shadow_receipt(
    db,
    signal={
        "symbol": c.symbol,
        "direction": c.direction,
        "confidence": float(c.score),
        "score": float(c.score),
        "rank": c.rank,
        "asset_class": c.asset_class,
        "gate_passed": c.gate_passed,
        "gate_blocker": c.gate_blocker,
        "prediction_id": c.prediction_id,
        "scan_id": scan_id,
        "source_layer": "day_trade_scanner",
    },
    market_data=None,
    lane="equity",
    requested_notional_usd=0.0,
    source="day_trade",
)
```
Wrapped in a defensive belt+braces `try/except` that logs `[day-trade-scan] ADL receipt schedule skipped` at DEBUG and continues. Lane is hard-coded `"equity"` per operator brief — the source tag `"day_trade"` is the primary discriminator from `crypto_paper_trader` and `trading_bot_service` ADL streams.

**Pre-signal early returns** (db missing → `scan_universe` returns `[]`, empty universe, db None) deliberately skip the loop entirely so no receipts are scheduled when no candidate exists.

**Hard rails (pinned by tests)**:
- 12-test net `tests/test_day_trade_scanner_adl_receipts.py` covers: APPROVED candidate produces receipt, blocked candidate (`below_min_score`, `non_directional_prediction`, `already_holding`) still produces receipt, signal payload contains `gate_passed`/`gate_blocker`, multi-candidate fan-out (3 candidates → 3 receipts), empty universe → 0 receipts, `db=None` → 0 receipts, raising helper does NOT alter `run_scan` result, helper return=`False` does NOT branch caller, plus two static checks (no `run_shadow_pipeline` direct call, no broker/executor imports anywhere in the file).
- Day-trade execution path is byte-equivalent: ScanResult shape, target writes, scan-log writes, gate iteration order — all unchanged.
- Code-size: `day_trade_scanner.py` 412 → 454 lines (+42, well under `core-governance` 600 preferred / 800 hard ceiling).

**Verified**:
- `tests/test_day_trade_scanner_adl_receipts.py`: **12 passed** in 0.15s.
- Targeted superset (test_day_trade_scanner + test_day_trade_scanner_adl_receipts + test_receipt_dispatch + test_crypto_paper_trader_adl_receipts): **50 passed** in 1.55s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2973 passed, 0 failed** (was 2961 + 12 new = 2973 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":569}`.

**Cumulative ADL coverage now spans**: equity executor (ADL-0 baseline at `trading_bot_service.execute_signal`), crypto paper bot (ADL-2 at `crypto_paper_trader.run_crypto_symbol`), day-trade scanner (ADL-3 at `day_trade_scanner.run_scan`).

**Remaining ADL micro-phases**:
- **ADL-4**: Wire options paper bot if present (`lane="options"`).
- **ADL-5**: Move/duplicate receipt earlier so kill_switch / risk_guard / sector_cap / max_trades_per_day / RoadGuard ENFORCE blocked decisions also produce receipts.

### ADL-2 — wire `crypto_paper_trader.py` to the shared receipt helper (2026-05-09)

**Micro-phase 2 of 5** of the operator-mandated ADL persistence-coverage fix. Closes the diagnosed crypto-lane gap (was 1.40% receipt coverage — 3/214 trades). Single early-fire `schedule_shadow_receipt` call covers APPROVED, HOLD, and gate-blocked paths uniformly.

**Patch site**: `services/crypto_paper_trader.py:run_crypto_symbol`. Insertion point is right after `signal = {**raw_signal, "symbol": symbol, "regime": ..., "failure_context": ...}` and BEFORE the "Early HOLD evaluation" branch. Because the call sits BEFORE every downstream gate (failure-memory cooldown, dynamic-confidence gate, adversarial veto, integrity mitigation, Patent J/K/M/I guard, Patent I legacy fallback, size-zero clamp, quote check), one call covers every decision path that actually has a strategist signal.

**Call shape** (verbatim):
```python
schedule_shadow_receipt(
    db,
    signal=signal,
    market_data=None,            # let extract_live_features fill live values
    lane="crypto",
    requested_notional_usd=0.0,  # pre-sizing snapshot
    source="crypto_paper_trader",
)
```
Wrapped in a defensive belt+braces `try/except` that logs `[crypto_paper] ADL receipt schedule skipped` at DEBUG and continues — even though `schedule_shadow_receipt` already swallows its own failures.

**Pre-signal early returns** (db_missing / not_crypto_symbol / insufficient_bars / ticker abandonment) deliberately do NOT call the helper — those have no signal to log.

**Hard rails (pinned by tests)**:
- 11-test net `tests/test_crypto_paper_trader_adl_receipts.py` covers: helper called on normal uptrend, lane=`"crypto"`, source=`"crypto_paper_trader"`, signal-with-symbol forwarded, `market_data=None`, `requested_notional_usd=0.0`, helper-NOT-called for db_missing / non-crypto / insufficient-bars, raising helper does NOT crash `run_crypto_symbol`, helper return=`False` does NOT branch caller, and a static check that the bot uses `schedule_shadow_receipt` (never `run_shadow_pipeline` directly).
- Crypto execution path is byte-equivalent: signal dict, sizing math, trade-row schema, audit-log writes, return shape — all unchanged.
- Code-size: `crypto_paper_trader.py` 1232 → 1256 lines (+24, soft-baseline drift only — preferred-ceiling test does NOT fail on growth of existing baseline entries).

**Verified**:
- `tests/test_crypto_paper_trader_adl_receipts.py`: **11 passed** in 0.78s.
- Targeted superset (test_crypto_paper_bot + test_crypto_paper_trading + test_crypto_paper_trader_adl_receipts + test_receipt_dispatch): **66 passed** in 2.30s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2961 passed, 0 failed** (was 2950 + 11 new = 2961 ✓).
- Backend boots clean — `/api/health` returns `{"status":"ok","db":"connected","routes":569}`.

**Remaining ADL micro-phases**:
- **ADL-3**: Wire day-trade scanner/executor (lane="equity").
- **ADL-4**: Wire options paper bot if present.
- **ADL-5**: Move/duplicate receipt earlier so kill_switch / risk_guard / sector_cap / max_trades_per_day / RoadGuard ENFORCE blocked decisions also produce receipts.

### ADL-1 — shared fire-and-forget receipt helper (2026-05-09)

**Micro-phase 1 of 5** of the operator-mandated ADL persistence-coverage fix. Pure helper extraction — zero behaviour change at the only existing call site, but makes ADL-2/3/4/5 trivial drop-in additions.

**New module** `services/ml/receipt_dispatch.py` (122 lines):
- `schedule_shadow_receipt(db, *, signal, market_data, lane, requested_notional_usd, ...)` — wraps the `asyncio.create_task(run_shadow_pipeline(...))` pattern with the surrounding try/except.
- Returns `True` if scheduled, `False` on ANY failure (no event loop, no Mongo, broken import, create_task raise). **Caller's execution path is identical either way**.
- Coroutines explicitly closed when `create_task` raises so failure paths don't leak unawaited-coroutine warnings.

**Refactored**: `services/trading_bot_service.py:execute_signal` Phase-5a block (24 inline lines → 11-line helper call). Verbatim args, verbatim fire-and-forget, verbatim warning-only failure.

**Hard rails (pinned by tests)**:
- 9-test net `tests/test_receipt_dispatch.py` covers: happy path (kwargs forwarded byte-for-byte), `db is None` short-circuit, no-running-loop short-circuit (sync caller / cron), `create_task` failure (caught + logged), broken `shadow_wiring` import, raising inner pipeline (caller still returns `True`), full byte-equivalence to legacy inline pattern.
- Static authority firewall: NO imports from broker / trading_bot_service / crypto_paper_trader / paper_trading_service / routes.broker / executors / RoadGuard / FastVeto / pipeline / broker_wire / kill_switch / alpha_decision_log.
- No Mongo write verbs (`insert_*`, `update_*`, `delete_*`, `drop`, `bulk_write`).
- No env reads (`os.environ` / `os.getenv` / `setenv`).

**Verified**:
- `tests/test_receipt_dispatch.py`: **9 passed** in 0.9s.
- `tests/test_trading_bot_broker_contract.py`: **14 passed** — broker contract net unchanged.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2950 passed, 0 failed** (was 2941 + 9 new = 2950 ✓).

**Remaining ADL micro-phases**:
- **ADL-2**: Wire `crypto_paper_trader.py` (lane="crypto").
- **ADL-3**: Wire day-trade scanner/executor (lane="equity").
- **ADL-4**: Wire options paper bot if present.
- **ADL-5**: Move/duplicate receipt earlier so kill_switch / risk_guard / sector_cap / max_trades_per_day / RoadGuard ENFORCE blocked decisions also produce receipts.

### `alpha_decision_log` persistence diagnostic + findings (2026-05-09)

**Operator-mandated upstream investigation** of the bottleneck the previous diagnostic surfaced. Read-only — no writes, no env mutation, no schema mutation, no retrain, no Phase 6 changes.

**New tool**: `python -m scripts.diagnose_alpha_decision_log_persistence --window-hours N`. Probes (read-only):
- Static call-site survey (which files invoke `record_decision` / `record_pipeline_decision` / `run_shadow_pipeline`?)
- ADL TTL + index inspection
- Write breakdown by lane / decision / blocked_at stage / symbol
- Per-lane gap analysis (paper trades vs ADL receipts)
- Recent supervisor-log scan for swallowed-exception warnings
- 8-hypothesis classifier + actionable recommendations

**Files**:
- `scripts/diagnose_alpha_decision_log_persistence.py` (267 lines, runnable CLI).
- `services/diagnostics/alpha_decision_log_persistence.py` (555 lines, probe/classifier library — under the 600 `core-governance` ceiling).
- `tests/test_diagnose_alpha_decision_log_persistence.py` (**16 tests passing in 0.43s**).

**Live findings (window=24h)**:
| Probe | Verdict |
|---|---|
| A. Persist hook never called? | NO — 2 call sites total (1× `run_shadow_pipeline`, 1× `record_pipeline_decision`) |
| **B. Single chokepoint?** | **YES** — only ONE caller (`trading_bot_service.execute_signal:428`, fire-and-forget). Every other executor (crypto_paper_trader, day_trade scanner/executor, options paper bot, signal-bot dispatcher tail) bypasses the hook. |
| **C. NO_TRADE silently dropped?** | **INVERTED** — every recorded receipt is NO_TRADE (perception-blocked). 0 APPROVED rows. The success path never produces receipts. |
| D. Lane imbalance? | NO — equity=4, crypto=3 (both lanes can produce rows when the hook is hit) |
| **E. Executors writing trades without receipts?** | **YES** — equity 4/185 (2.16%), crypto 3/214 (1.40%). ~98% of trades fire without producing any receipt. |
| F. TTL purge pressure? | NO — TTL 30.0d, well above any sensible window |
| G. Swallowed exceptions in logs? | NO observed (but absence of warnings is NOT proof — exceptions are silently caught in `record_decision` + the create_task callback) |
| **H. Narrow symbol coverage?** | **YES** — only 2 distinct symbols (AAPL, BTC-USD) ever produced a receipt |

**Root cause confirmed**:
1. `record_decision` is called from EXACTLY ONE place (`shadow_wiring.py:280`).
2. `run_shadow_pipeline` is called from EXACTLY ONE place (`trading_bot_service.execute_signal:428`).
3. That call site is `_asyncio_p5a.create_task(...)` — fire-and-forget, never awaited.
4. The legacy executor path is the only entry to the shadow wiring. Every other executor (crypto_paper_trader, day-trade pipeline, options paper bot, signal-bot tail paths) writes paper trades but produces ZERO ADL receipts.

**Recommendations surfaced (priority order)**:
1. **PRIMARY**: Add fire-and-forget `run_shadow_pipeline` calls at every executor entry point — crypto_paper_trader, day_trade scanner/executor, options paper bot, signal-bot dispatcher (where it bypasses execute_signal).
2. Crypto executor specifically — confirm by inspection then patch.
3. Equity day-trade executor — same.
4. (Auto-suppressed when not applicable): NO_TRADE-decisions-only inversion → move the persist call EARLIER in the pipeline so upstream gates (kill_switch, risk guard, sector cap) ALSO produce receipts.

**Verified**:
- `tests/test_diagnose_alpha_decision_log_persistence.py`: **16 passed** in 0.43s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2941 passed, 0 failed**.
- Live CLI reproduces every finding.

### `no_decision_log` skip-rate diagnostic + findings (2026-05-09)

**Operator-mandated diagnosis** of why retraining only matched 5/1445 paper trades. Built a read-only `python -m scripts.diagnose_alpha_retrain_join` tool that probes all 8 hypothesis questions verbatim. **No writes, no env mutation, no schema mutation, no retrain promotion** — strict observation.

**Live findings (window=30d)**:
| Probe | Verdict |
|---|---|
| 1. Trades pre-date ADL? | YES — 1544 unmatched trades closed before oldest ADL row |
| 2. Unstable join keys? | YES — `paper_trades` uses `'ticker'`, `crypto_paper_trades` uses `'symbol'`, `alpha_decision_log` uses `'symbol'` |
| 3. Wrong timestamp field? | N/A — join is keyed by `(symbol, lane)`, time only bounds the read window |
| 4. Symbol normalization mismatch? | YES — 194 crypto rows would match if `BTC` ↔ `BTC-USD` |
| 5. Lane mismatch? | YES — neither paper-trade collection has a `lane` field; both default to "equity" |
| 6. ADL only for Phase 5a+? | YES — ADL spans 0.40d but window is 30d |
| 7. ADL TTL too short? | LIKELY — only 0.40d of history; persistence layer brand new |
| 8. Crypto vs equity schema drift? | YES — symbol field `ticker` vs `symbol`; pnl `pnl_usd` vs `pnl` |

**Match outcome**: 50/1998 in-window paper trades match (2.50%). 1544 trades pre-date ADL; 210 have no ADL row at all; 194 are normalization-mismatches.

**Real bottleneck identified**: `alpha_decision_log` has only **7 rows total** across **2 distinct keys** (`AAPL/equity`, `BTC-USD/crypto`), span 0.40d. The ADL persistence layer (in `services/ml/shadow_wiring` or `pipeline.py`) is barely firing. NO BACKFILL is possible — upstream features can't be recreated.

**Files**:
- `scripts/diagnose_alpha_retrain_join.py` (279 lines) — runnable CLI + report formatter.
- `services/diagnostics/alpha_retrain_join.py` (478 lines) — probes/classifiers/recommendations library (split out + relocated under `services/diagnostics/` since it's a library, not a runnable script — fits the 600-line `core-governance` ceiling).
- `services/diagnostics/__init__.py` — package docstring with the read-only authority-boundary contract.
- `tests/test_diagnose_alpha_retrain_join.py` — **28 tests** pinning pure helpers, hypothesis classifiers, recommendations, and a static check that the diagnostic has no broker/executor/RoadGuard/FastVeto imports and zero Mongo write verbs (`insert_*`, `update_*`, `delete_*`, `drop`, `bulk_write`, ...).

**Recommendations surfaced (priority order)**:
1. **PRIMARY**: ADL persistence layer is the real blocker — investigate `services/ml/shadow_wiring`/`pipeline.py` to confirm it fires on every decision.
2. Patch `extract_rows()` to read `symbol or ticker or pair`.
3. Derive `lane='crypto'` for `crypto_paper_trades` / `asset_class='crypto'` rows.
4. Add symbol-normalization step (`BTC` → also probe `BTC-USD`).
5. Patch `label_from_outcome()` to accept `pnl_usd` / `pnl` / `r_multiple` (not just `realized_pnl_usd`).
6. **DO NOT** run v2 retrain `--write-artifact` yet — 2.5% coverage trains a non-representative slice.

**Verified**:
- `tests/test_diagnose_alpha_retrain_join.py`: **28 passed** in 0.12s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2925 passed, 0 failed**.
- Live CLI: produces the full hypothesis report + match-attempt + sample rows + recommendations on real data.

### v2 retraining schema — `alpha_v2_fundamentals_technicals` (2026-05-09)

**Operator-mandated next step** after the feature builders. Extends `scripts/retrain_alpha_models.py` to consume `frame.market.fundamentals.*` + `frame.market.technicals.*` while leaving live inference completely untouched.

**Schema layout**:
| Schema | Dim | Layout |
|---|---:|---|
| `alpha_v1` (DEFAULT, unchanged) | 10 | perception 6 sub-scores + avg_conf + recall ±ratios + intent_encoded |
| `alpha_v2_fundamentals_technicals` | **40** | v1 (10) + fundamentals (16, equity-only) + technicals (14, both lanes) |

**Lane semantics (pinned by tests)**:
- **Equity rows**: fundamentals + technicals both expected; missing → 0.0 fills + counter bumped.
- **Crypto rows**: fundamentals NOT required (no skip, no counter bump); technicals expected.
- **Pre-feature-builder historical rows** (no `frame.market` payload): trainable under v2 — slots fill 0.0, counters bump. Historical training data is NEVER abandoned.
- **v1 contract preserved**: `_FEATURE_DIM == 10` Strategist invariant still pinned. v1 default everywhere.

**Files**:
- `scripts/retrain_alpha_models.py`: 607 → 755 lines (under the 800 architectural ceiling).
- `scripts/_retrain_v2_schema.py`: new, 218 lines. Strangler-split holds the v2 schema constants + ordered feature names + `reconstruct_features_v2`. Re-exported from the main script — external imports unchanged.

**Report / manifest enrichment** (always present, zero on v1):
- `feature_schema_version`
- `feature_count` + `feature_count_v1_baseline`
- `feature_names` (full ordered list)
- `missing_fundamentals_count` (equity-only)
- `missing_technicals_count`
- Manifest filename includes the schema tag (`strategist_alpha_v2_fundamentals_technicals_<sha>_<ts>.joblib`) — operator can never confuse v1/v2 by inspection.
- Manifest field `promotion_blocked_reason="v2_schema_requires_matching_inference_adapter"` for v2; `null` for v1.

**Hard rails (pinned by tests)**:
- v2 retrain MUST NOT mutate `STRATEGIST_ARTIFACT` / `AUDITOR_ARTIFACT` env vars (sentinel test).
- No broker / executor / RoadGuard / FastVeto / pipeline / kill-switch imports in either script (static check).
- No `Verdict.X` / `set_active` / `promote_now` / `.place_order` markers (static check).
- v2 artifacts NEVER auto-promoted. Live inference stays on v1 until a v2 inference adapter is explicitly built.

**CLI**:
```
python -m scripts.retrain_alpha_models --feature-schema alpha_v2_fundamentals_technicals \
    [--window-days N] [--write-artifact] [--json out.json]
```
Default schema is `alpha_v1` so existing automation is byte-equivalent.

**Verified**:
- `tests/test_retrain_alpha_models.py`: **31 passed** (17 v1 baseline preserved + 14 new v2 tests).
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 86.
- Full pytest: **2897 passed, 0 failed**.
- Live CLI smoke (`--feature-schema alpha_v2_fundamentals_technicals --window-days 30`): runs cleanly, prints `feature count: 40 (v1 baseline = 10)`, prints the v2 promotion-block warning at the bottom of the report.

### Feature builders — fundamentals + technicals (2026-05-09)

**Operator directive**: P/E + SMA logic must be a **feature/input layer**, never a decision authority. Built two new modules under `services/ml/features/` that emit structured feature dicts for the ML perception/strategist layers to consume during retraining.

**New package** `services/ml/features/`:
| File | Lines | Authority |
|---|---:|---|
| `__init__.py` | 41 | Package docstring + authority-boundary contract |
| `fundamentals.py` | 168 | Equity-only AV-OVERVIEW shaper (P/E, EPS, PEG, dividend yield, ROE, profit margin, growth YOY, beta, book value, EV/EBITDA, …) + binary band flags (`pe_in_value_band`, `pe_in_growth_band`, `pe_in_speculative`, `negative_eps`, `dividend_payer`) |
| `technicals.py` | 123 | Universal (equity + crypto) wrapper around `services.universe_technicals.compute_technicals` — RSI14, MACD, SMA20/50/200 + binary flags (`price_above_sma20/50/200`, `sma20_above_sma50` golden-cross proxy, `rsi_oversold` / `rsi_overbought` / `rsi_neutral`, `macd_bullish` / `macd_bearish`) |

**Wiring**: nested into `extract_live_features` as observation-only keys (`frame.market.fundamentals`, `frame.market.technicals`). Empty results are omitted (no littering the frame with empty dicts).

**Hard rails (pinned by tests)**:
- **Authority firewall**: feature builders import NOTHING from `broker_service`, `trading_bot_service`, `crypto_paper_trader`, `paper_trading_service`, `routes.broker`, `services.ml.{executors, roadguard, fast_veto, shadow_wiring, pipeline, broker_wire}`, `services.alpha_decision_log`, `ai_core.kill_switch`. Pinned by source-level regex check.
- **No verdicts**: source contains no `BUY` / `SELL` / `NO_TRADE` / `Verdict.*` / `set_active` / `promote_now` / `place_order` / `broker.execute`. Pinned by static substring check.
- **Equity-only firewall on fundamentals**: crypto / unknown / blank lanes return `{}` (no garbage P/E for digital assets). Pinned in 5 tests.
- **Never raises**: provider exception, `None` return, empty dict, blank symbol — all yield `{}`. Pinned in 6 tests.
- **Strategist vector dim unchanged**: `_FEATURE_DIM == 10` invariant test pinned. Static check that `_build_features` source does NOT read `fundamentals` / `technicals`. Existing artifact compatibility preserved — these features are observation-only today; available for the next retrain.

**Verified**:
- `tests/test_ml_feature_builders.py`: **45 passed** in 1.45s.
- `make lint-arch` 9 · `make lint-fast` 133 · `make lint-safety` 72.
- Full pytest: **2883 passed, 0 failed** (was 2838 + 45 new tests).
- Live smoke: `extract_live_features("AAPL", "equity")` returns nested `fundamentals` (19 numerics + 5 band flags) and `technicals` (9 indicators + 10 flags) on top of the existing flat keys (`broker_uptime`, `intraday_progress`, …) — no shadowing.
- Backend boots clean.

**Aligns with**: dual-stack architecture, adversarial layering, RoadGuard separation, shadow-first rollout, artifact-based retraining flow. Avoids the "single hardcoded brain" failure mode the user spent weeks removing from Camaro.

### Pre-4E broker-call contract net (2026-05-09)

**Operator-mandated stabilization pass** before the riskiest slice. Step 4E is **PAUSED** until this safety harness exists. Goal: pin the observable contract of the broker call surface so any future move/refactor that drifts it fails loud and fast.

**New file** `tests/test_trading_bot_broker_contract.py` — **14 tests, 0.78s**, fully mocked at the boundary (zero real broker calls, zero Mongo, zero env mutation).

**Snapshot scope** (per operator brief):
1. **Broker call ORDER**: `db.broker_connections.find_one` → `_get_user_broker` → `_get_or_refresh_client` → `place_order`. Pinned by spy log assertion.
2. **place_order kwargs SHAPE**: `{symbol, qty, side=lower(), order_type="market", time_in_force="day"}`. Pinned by `call_args.kwargs` exact match.
3. **Sync-call discipline**: `place_order` is wrapped in `asyncio.to_thread`. Direct-await regression would fail the test.
4. **Error paths** (every short-circuit branch):
   * `_db is None` → `{"error": "DB unavailable"}` (no broker import attempted — tripwire monkeypatched).
   * No broker connection → `{"error": "No broker connected for live trading"}`.
   * Falsy `place_order` result → `{"error": "Broker rejected order"}`.
   * Any exception → `{"error": f"Live execution failed: {e}"}` (sanitized; raw exception never escapes).
   * Unknown mode → `{"error": "Unknown bot mode: <mode>"}`.
5. **Return shapes** (success): `{"status": "filled", "broker_order_id", "symbol", "side", "qty"}` exact match.
6. **Paper-mode firewall**: paper path passes `side.upper()`, forwards SL/TP, AND must NOT touch `broker_connections` or import `routes.broker`. Pinned by spy + tripwire.
7. **Risk-guard pre-flight**: runs strictly BEFORE any broker lookup; adjusted qty propagates to `place_order`. `_apply_bot_risk_guards` independently pinned: DB-None fail-open, halve-on-trip with floor 1.0, exception fail-open with original qty preserved.

**Synthetic regression smoke test** (verified): replacing `side=side.lower()` with `side.upper()` in the live branch failed 2 contract tests by name; restoring → all 14 green again.

**Wired into `make lint-fast`**: net now runs as part of the broader Phase 6 architectural-invariant bundle (was 119 tests, now **133** — +14, sub-second runtime preserved at 0.95s).

**Hard rails honoured**:
- No broker code moved.
- `BROKER_LIVE_ORDER_ENABLED` unchanged (stays `false`).
- Phase 6 promotion stays blocked.
- No env mutation, no schema change, no allowlist additions.

**Step 4E remains PAUSED** until operator command. The contract net is now the gate it must clear.

### Step 4D — `trading_bot_service.py` decision-rules extraction (2026-05-09)

**Micro-phase 4 of 5**. Pure decision rules only — zero behaviour change. No DB, no broker, no mutation, no kill-switch state changes. Skip/block reasons unchanged.

`services/trading_bot_service.py`: 1402 → **1338 lines** (-64).

**Moved** to `services/trading_bot/_decision_rules.py` (108 lines):
| Function | Authority |
|---|---|
| `get_total_exposure` | Sum USD-notional size across open positions. Pure. |
| `get_open_trade_count` | `len(open_positions)`. Pure. |
| `get_sector_exposure` | Sector-share ratio (0.0–1.0). Pure. |
| `apply_portfolio_constraints` | Eligibility/headroom check returning a non-negative trade size (0 = block, "portfolio limits reached" reason left in caller). Pure. |

**Skipped from 4D scope** (deferred — not pure):
- `_check_kill_switch_and_drawdown` — calls `kill_switch.activate(...)` (state mutation; hybrid decision+activation).
- `_compute_adjusted_size`, `_resolve_qty`, `_extract_trade_size` — sizing math (out of "decision rules" scope per operator constraint).
- `_bot_from_config` — execution-prep synthetic-bot builder.

**Constants**: `MAX_CONCURRENT_TRADES`, `MAX_PORTFOLIO_EXPOSURE`, `MAX_SECTOR_EXPOSURE_PCT` imported from `_constants` directly inside the moved module. No existing test monkeypatches `tbs.MAX_*`, so this is byte-equivalent for all current callers and tests.

**Compatibility shim**: re-imported under public names so `test_portfolio_risk_engine.py` (`tbs.get_total_exposure`, `tbs.apply_portfolio_constraints`, etc.) and all in-module bare-name calls work unchanged.

**Verified**:
- `make lint-arch` 9 passed · `make lint-fast` 128 passed · `make lint-safety` 72 passed.
- `test_portfolio_risk_engine.py`: 35/35 passed.
- Targeted superset (execute_signal + executor_lanes + roadguard + fast_veto + trading_bot + portfolio_risk + adaptive_sizing + drawdown): **202 passed**.
- Full pytest: **2824 passed, 0 failed** (identical pre/post extraction).

**Cumulative trading_bot_service.py**: 1575 → 1338 lines (-237, -15%) across 4A + 4B + 4C + 4D.

**Remaining**: 4E (broker calls — LAST, riskiest slice). Operator-mandated PAUSE before 4E.

### Step 4C — `trading_bot_service.py` data-access extraction (2026-05-09)

**Micro-phase 3 of 5**. Bot-CRUD helpers only — zero behaviour change. No broker calls, no decision logic, no sizing, no Mongo query / update / response shape changes.

`services/trading_bot_service.py`: 1506 → **1402 lines** (-104).

**Moved** to `services/trading_bot/_data_access.py` (171 lines):
| Function | Authority |
|---|---|
| `create_bot` | Insert bot doc (defaults OFF). |
| `_build_config` | Per-bot-type config validator/builder (no DB). |
| `toggle_bot` | Flip ``enabled`` flag. |
| `update_bot_config` | Merge config patch. |
| `delete_bot` | Delete by `(user_id, bot_id)`. |
| `get_user_bots` | List user's bots. |

**Compatibility shim**: re-imported under the original public names so external callers (`routes/trading_bots.py` does `from services.trading_bot_service import create_bot` etc.) and tests are unchanged. The moved helpers fetch the live `_db` via the existing `_resolve_db_for_shadow` getter (deferred import to break the circular dep — same pattern as 4B). Mongo queries, projections, sort orders, and return shapes are byte-equivalent.

**Verified**:
- `make lint-arch` 9 passed · `make lint-fast` 128 passed · `make lint-safety` 72 passed.
- Targeted suite (execute_signal + executor_lanes + roadguard + fast_veto + trading_bot + portfolio_risk + adaptive_sizing + drawdown + webhook + grid): **207 passed**.
- Full pytest: **2824 passed, 0 failed** (identical pre/post extraction).
- Backend boots clean.

**Cumulative trading_bot_service.py**: 1575 → 1402 lines (-173, -11%) across 4A + 4B + 4C.

**Remaining**: 4D (decision rules), 4E (broker calls — LAST).

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
- **Fundamental + Technical Feature Enrichment Engine** (saved 2026-05-09, NOT wired). File: `services/ml/features/_backlog/fundamental_technical_engine.py`. Combined dataclass output (PE/EPS, SMA5/20, slopes, volatility, regime tag, feature health). Open questions before wiring: where does `net_income`/`shares_outstanding` come from at runtime, does `regime_tag` replace or supplement existing tagger, does its `feature_health` override `services/ml/feature_health.py`. Operator decree: review against existing `fundamentals.py`/`technicals.py` overlap before any wiring.

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
