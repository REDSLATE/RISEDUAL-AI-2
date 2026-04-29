# RISEDUAL AI — Changelog

## 2026-04-29 — Candidate AI Core Engine + Registry

User asked for a true parallel candidate engine running side-by-side
with the live AI Core, observing the same firehose with a different
schema, with manual promotion + advisory alert (option **c**).

### Backend changes
- `services/ai_core_engine.py` refactored:
  - `LearningEngine.__init__` now takes ``name`` + ``schema``;
    persistence collection derived from name (legacy ``live`` keeps
    ``ai_core_trades`` for back-compat; others get
    ``ai_core_engine_{name}_trades`` so engines can never overwrite
    each other).
  - New `SchemaConfig` class with ``confidence_buckets`` + ordered
    ``dimensions`` list; ``extract_conditions()`` is the single
    extension point for new bucketers.
  - New `LearningEngineRegistry` with ``register/get/all/names/promote/
    broadcast_trade/reset_all/set_db_for_all`` and ``.live`` property.
  - Two engines registered at module load:
    - **`live`** (5-bin confidence; 4 dimensions: regime, agent,
      asset_type, confidence_bucket)
    - **`candidate_v2`** (6-bin confidence with split 80-85/85-90;
      adds `direction_family` and `confidence_x_agent` cross dim)
  - `learning_engine` symbol kept as alias for ``registry.live``
    so existing imports keep working.
- `services/ai_core_autowire.py` now broadcasts via
  `registry.broadcast_trade(...)` so every new ingestion lands on
  every engine.
- `routes/ai_core_routes.py`:
  - `POST /api/ai-core/trade` now broadcasts; response carries
    `engines` map alongside back-compat live result.
  - `POST /api/ai-core/reset` resets every engine.
  - **NEW** `GET /api/ai-core/engines` — side-by-side stats for all
    engines.
  - **NEW** `GET /api/ai-core/engines/compare?dimension=agent` —
    bucket-lift comparison (max-min win-rate spread).
  - **NEW** `GET /api/ai-core/engines/{name}` — single engine.
  - **NEW** `POST /api/ai-core/engines/{name}/promote` — admin-only
    label flip; previous live becomes candidate (never retired).
- `server.py` nightly cron now hydrates every engine and emits a
  dedup-safe **`bridge_eligible_{name}`** advisory alert when a
  candidate beats live by ≥ 0.05 lift on the `agent` dimension over
  ≥ 100 samples. Operator remains the only one who can promote.

### Promotion is registry-level, NOT firewall-crossing
Per dual-stack spec: promoting a candidate to live changes which
engine is the official scoreboard, but does NOT make AI Core stats
flow into DTD components. Council still reads prediction-tracker
stats. Cross-firewall feedback would require a registered
Promotion Bridge.

### Tests — 44/44 pass in 0.13s
- New `tests/test_ai_core_registry.py` (11): registry shape, schema
  divergence, candidate cross-dim extraction, broadcast fan-out,
  promotion semantics, isolated reset.
- All 33 prior tests (engine + alerts + dual-stack invariants) green.

### Live findings (immediate)
candidate_v2's `confidence_x_agent` cross dimension reveals the toxic
spike root-cause cleanly:
- **`signal_dispatcher@90-100` = 0/45 wins (toxic)**
- `signal_dispatcher@80-85` = 0/31; `@70-80` = 0/25
- `market_prediction@0-60` = 72%; `@60-70` = 68%
- `war_room@0-60` = 100% over 48
- `hypothesis` = 100% across all buckets (34 trades)

This is exactly the per-(agent, confidence) blame-tally the user
wanted, now visible at `/api/ai-core/engines/candidate_v2` without
waiting for the candidate to clear a bridge.



## 2026-04-28 (c) — Dual-Stack Hardening (Build Order v1)

User formalised the AI Core / Council separation into a patent-grade
dual-domain spec. Implemented the enforcement infrastructure end-to-end.

### New canonical document
- `/app/memory/RISEDUAL_DUAL_STACK_SPEC.md` — frozen v1.0. Codifies
  DTD vs PRD, the firewall, the promotion bridge, and the autonomous
  evolution loop. Future changes require version bumps.

### New backend modules (all opt-in; existing code untouched)
- `services/role_scoped_db.py` — three capability-restricted Mongo
  handles: `DtdClient`, `PrdReadOnlyClient`, `BridgeCalibrationClient`.
  Each has explicit `allow_rw`/`allow_ro`/`deny` collection sets;
  forbidden ops raise `PermissionError` at the call site.
- `services/firewall.py` — the only DTD → PRD ingress. `publish_resolved()`
  appends to immutable `prd_resolved_outcomes` (unique index on
  `outcome_id`); rejects unsettled payloads (configurable settle
  window); `read_resolved()` is the read-only egress.
- `services/dtd_replay_channel.py` — append-only `dtd_decision_replay`
  collection (unique index on `decision_id`). Mirrors every DTD decision
  for audit, deterministic backtest, candidate benchmarking. `record_decision()`
  is DTD-side; `read_replay()` is PRD-side.
- `services/promotion_bridge.py` — the only PRD → DTD path. Bridges are
  registered in code with `BridgeSpec(name, version, output_target,
  output_bounds, min_samples, oos_window_days, regression_threshold)`.
  Defaults to **empty + inactive**. Activation requires
  `BRIDGE_APPROVAL_TOKEN` env match + evidence above thresholds + value
  within bounds. Every activation/revocation appended to
  `bridge_activations` audit log. `get_calibration(name)` returns
  `None` when inactive (DTD callers default to unmodified parameter).

### Domain tags applied
- `__domain__ = "PRD"` on `ai_core_engine.py`, `ai_core_alerts.py`,
  `ai_core_autowire.py`.
- `__domain__ = "BRIDGE"` on `firewall.py`, `role_scoped_db.py`,
  `promotion_bridge.py`.
- `__domain__ = "DTD"` on `dtd_replay_channel.py`.
- Backfilling remaining legacy modules tracked as a follow-up.

### Tests — 33/33 pass in 0.26s
- `tests/test_dual_stack_invariants.py` (18) — collection-domain
  exclusivity, DTD client denies PRD/BRIDGE, PRD client read-only on
  DTD + denied on BRIDGE, Bridge client capability matrix, firewall
  settle-window rejection, missing-field rejection, replay
  missing-field rejection, bridge registry empty-by-default, bridge
  approval-token enforcement, bridge bounds clamp, bridge evidence
  thresholds, bridge round-trip activation/revocation, domain-tag
  presence, **PRD-imports-DTD grep audit** (catches accidental
  cross-domain imports at CI time).
- Existing `test_ai_core_engine.py` (10) + `test_ai_core_alerts.py` (5)
  remain green.

### Result
Boundary that was a doc convention is now:
1. role-scoped DB handles → reject cross-domain operations at handle
2. append-only collections → unique indexes prevent retroactive edits
3. CI invariant tests → grep audit + capability tests run on every push
4. explicit bridge registry → only documented PRD→DTD path; off by default



## 2026-04-28 (b) — AI Core Learning Engine

User pasted a patch from a sister project that fixed two test failures
in an `ai_core` route module. RISEDUAL didn't have that module — but
the *shape* (in-memory LearningEngine + Mongo persistence + nightly
sweep + dedup-safe daily alerts) is genuinely useful. Built it as a
net-new module here.

### Backend
- New `services/ai_core_engine.py` — singleton `LearningEngine` with
  `trade_log` (1000-entry deque), aggregate `stats` (total_resolved,
  wins, losses, flats, pending, rejections), and `condition_stats`
  bucketed on (regime, agent, confidence_bucket, asset_type).
  Idempotent ingestion via `trade_key = source:source_id`. Cold-start
  hydrates from Mongo so a backend restart doesn't lose stats.
- New `services/ai_core_alerts.py` — date-bucketed dedup writer to
  `ai_core_alerts` collection. Alert id = `{type}:{YYYY-MM-DD}` with
  unique index, so same-day re-fires return `deduped: True` instead
  of duplicating rows.
- New `services/ai_core_autowire.py` — pulls from existing
  `paper_trades` (`status=closed`, `outcome` ∈ {win, loss, flat})
  and verified `predictions` (`verified_24h.correct ∈ {True, False}`)
  into the engine. Uses the engine's idempotency so repeat sweeps
  ingest only the genuinely new resolutions.
- New `routes/ai_core_routes.py` exposing `/api/ai-core/{stats,
  trades, alerts, trade, reject, cron/nightly, reset}`. `/reset` is
  open in `ENV=development`, admin-only otherwise. `/cron/nightly`
  is admin-only.
- Scheduler hook in `server.py` — daily at 02:45 UTC (after memory
  cleanup and ML retrain) so newly-graded predictions / closed
  paper trades are picked up.

### Tests — 22/22 pass
- `test_ai_core_engine.py` (10) — outcome canonicalisation, confidence
  bucketing (mixed 0-1 / 0-100), idempotent ingest, condition aggregates,
  win-rate excludes flats, reset semantics.
- `test_ai_core_alerts.py` (5) — first emit inserts, same-day re-emit
  dedups, different date_bucket creates separate row, list ordering,
  no-db graceful path.
- `test_ai_core_routes.py` (7) — live API: auth gating, /reset wipe,
  /trade idempotency, pending rejection, /cron/nightly emit + same-day
  dedup, /reject log, /trades pagination.

### First live numbers (admin manual trigger)
First sweep ingested **269 resolved trades** (5 paper + 264 prediction)
with overall **52.4 % win-rate**. Per-agent breakdown immediately
revealed `signal_dispatcher` at **0/101 win-rate** vs. `market_prediction`
at **70 % over 77 trades** — a useful signal that's now visible at a
single endpoint instead of being scattered across collections.



## 2026-04-28 — Toxic Spike Autopsy

User shared the "56 Bad Predictions Detected — Persisting (2 days
in a row)" alert and asked for a drill-down to see **why** the
high-confidence predictions failed, not just **that** they did.

### Backend
- New `services/symbol_sector_resolver.py` — Mongo cache (7-day
  TTL) → curated static map (60+ big-cap tickers) → Finnhub
  `/stock/profile2` fallback → "Unknown". Bulk variant runs with
  concurrency-8 semaphore so admin-panel opens don't burst Finnhub.
- New `services/toxic_autopsy_service.py` — `build_autopsy(db,
  days, min_confidence_pct)` queries `predictions` for
  `verified_24h.correct == False` rows in the window, normalises
  confidence scale (0-1 and 0-100 both handled via
  `prediction_tracker.normalize_confidence`), and groups by
  failure_code / feature (agent origin) / confidence-bucket
  (80-85, 85-90, 90-95, 95-100) / direction family / sector /
  model version / grade. Returns top-offender symbols ranked by
  count + avg confidence, plus up to 200 raw samples.
- New `routes/toxic_autopsy.py` exposing
  `GET /api/admin/toxic-spike/autopsy?days=2&min_confidence=80`
  (admin/owner only, 403 for non-admins).
- Registered `toxic_autopsy_router` + `set_toxic_autopsy_db`
  in `route_registry.py`.

### Frontend
- New `components/admin/ToxicSpikeAutopsyPanel.jsx` — summary
  header (total failures, unique symbols, avg confidence), six
  breakdown sections rendered as horizontal bars (count + pct +
  avg-conf per key), Top Offenders table (symbol, sector, fails,
  avg conf, top failure codes, top agents), and a collapsible
  Raw Failures table (prediction_id, symbol, sector, agent,
  direction, confidence, grade, failure_code, entry/verified
  prices). Days + min-confidence selects re-fetch on change.
- New `autopsy` tab in `AdminPanel.jsx` Insights group
  (AlertTriangle icon, data-testid infrastructure on every
  interactive element).
- Deep-link from the toxic-spike notification — admin-only
  "View Autopsy" button on `ToxicSpikeNotification` dispatches
  a `risedual:open-admin-autopsy` custom event and writes a
  tab hint into `sessionStorage`. `AuthenticatedShell` listens
  for the event to open the Admin modal; `AdminPanel`'s
  initial-tab `useState` reads the one-shot hint on mount and
  lands on the autopsy tab.

### Tests
- 12 unit tests (`test_toxic_autopsy.py` + `test_symbol_sector_resolver.py`)
- 11 API integration tests + 7 sector-resolver tests added by
  testing subagent (iteration 147). Total 30 tests — 100% pass.
- Verified: confidence scale normalisation (mixed 0-1 / 0-100
  writes collapse correctly), NEUTRAL grades excluded, low-conf
  misses excluded, query params honoured, six breakdown
  dimensions populated, top offenders sorted, sector fallback
  chain works, ops-snapshot regression still clean.



## 2026-02-08 (k) — CLI prototype port: Ops Snapshot + ECE + Heuristic Notes

User shipped a `risedual` CLI prototype zip (image-classifier
adversarial-training package) and asked which patterns were
portable. Three pure-function patterns were adapted into the
production admin surface; the actual ML training code stays in
the prototype as research-only.

### A — Ops Snapshot
- New `services/ops_snapshot.py` — adapts the prototype's
  `env_state.py` pattern: known operator flags (always reported,
  set or unset), auto-detected prefixed flags (RISEDUAL_/CRYPTO_/
  COUNCIL_/etc.), real Mongo ping probe, scheduler-heartbeat freshness
  check, integration-key configured booleans (never the values
  themselves), and Tier 3 readiness pulled from the existing
  shadow stats service.
- New `routes/ops_snapshot.py` exposing `GET /api/admin/ops-snapshot`
  (admin/owner only).
- New `OpsSnapshotPanel.jsx` admin tile under the Operations →
  Health tab (HeartPulse icon, `data-testid="admin-tab-ops"`).
- Heuristic notes layer auto-generates single-sentence operator
  guidance: "Mongo ping failed", "Scheduler appears stalled",
  "USPTO_API_KEY not set", "COUNCIL_RISK_MODULATOR_ENABLED=true
  but Tier 3 is locked", or "All gauges nominal."
- 18 unit tests + 17 API integration tests = 35 tests on this
  path alone.

### B — ECE in calibration
- Adapted the prototype's `auditor._ece` weighted-bin formula
  into `routes/admin.py::get_conviction_calibration`. Endpoint
  now returns top-level `ece: {conviction, confidence}` numeric
  fields alongside the existing buckets.
- Heuristic notes added: "Conviction ECE = X.XXX (poor)" / borderline
  / healthy; non-monotonicity flagged separately so the operator
  sees structural failure first.
- **Live finding on real data:** Conviction ECE = 0.534, Confidence
  ECE = 0.478 — both well above the 0.10 "poor" threshold AND
  conviction win-rate is non-monotonic. CONVICTION_WEIGHTS are
  meaningfully miscalibrated; this is exactly the signal the
  prompt was for.
- 8 unit tests in `test_calibration_ece.py`.

### C — Heuristic notes on shadow stats
- New `_shadow_stats_notes` in `services/research_shadow_stats.py`;
  `compute_shadow_stats` response now includes a `notes: [str]`
  array.
- Branches: no decisions yet, no actionable buckets, high-winrate
  + low samples (lucky early run guard), low-winrate + sufficient
  samples (engine actively wrong), all healthy.
- **Live finding:** adversarial+crypto bucket at 92% win-rate
  over only 12 dissents, correctly flagged "wait for sample size
  before acting" — guards against premature Tier 3 promotion.
- 8 unit tests in `test_shadow_stats_notes.py`.

### Testing
- 266/266 unit tests across all 15 touched suites pass.
- Testing agent: 100% backend (50/50), 100% frontend (all
  8 data-testid elements verified). Zero issues, no retests.

## 2026-02-08 (j) — P1/P2 backlog sweep (items #1, #2, #4, #5)

### #2 — Paper-trader duplicate-insert race fix
- New `services/ml_paper_trader.ensure_indexes(db)` creates a
  unique partial index on `(ticker, direction, prediction_id,
  time_bucket)` where `time_bucket = floor(opened_at_unix / 60)`.
- `_time_bucket_for(now)` injected into every new doc; insert
  path catches `pymongo.errors.DuplicateKeyError` and returns
  the existing trade_id instead of erroring.
- Wired into `route_registry.py` startup hook.
- The 12-second AAPL twin scenario from 2026-04-16 is now
  physically impossible at the DB level.
- Tests: 6 unit tests in `test_ml_paper_trader_idempotency.py`.

### #4 — Backtest/Live data labeling
- New `services/data_source_labeler.py` — read-side annotator
  that attaches a `data_source: "live" | "backtest"` field
  based on `PUBLIC_DATA_FLOOR_DATE` env (default `2026-04-23`).
  Priority order: opened_at > predicted_at > created_at > timestamp.
  Naive datetimes assumed UTC; missing timestamps default to "live".
- Wired into `GET /api/ml/paper-trades` and `GET /api/accuracy/history`;
  both endpoints now also return `data_floor_date` at top level.
- `MLPaperPnL.jsx` renders a "Backtest" badge on pre-floor rows
  (data-testid `ml-trade-{i}-backtest-badge`, tooltip explains
  the cutover).
- Tests: 17 unit tests in `test_data_source_labeler.py`.
- **Operator dial:** rotate the floor by setting
  `PUBLIC_DATA_FLOOR_DATE` in `.env` to a different ISO date.

### #1 — `/api/crypto/sltp-expectancy` analytics endpoint
- New `services/crypto_sltp_expectancy.py` — read-only analytics
  over `crypto_paper_trades` (status=closed). Returns expectancy,
  win rate, breakdown by close_reason / direction / regime, plus
  tighter-bracket what-ifs (we only project tighter, not wider —
  wider needs OHLC tick data we don't store).
- 20-sample minimum guard so tiny windows don't surface noise.
- Live data on the existing 98 closed crypto trades reveals
  expectancy=-0.06R, 56.1% win rate, with 80/98 trades exiting
  via `hold_window_expired` (zero `take_profit` fires).
- Headline picks the strongest signal from the data — current
  copy reads: "Data suggests tightening SL to 30% of current
  would lift expectancy from -0.06R to +0.14R."
- Admin-only at `GET /api/crypto/sltp-expectancy?days=30`.
- Tests: 17 unit tests in `test_crypto_sltp_expectancy.py`.

### #5 — Polygon.io adapter wired into market_data_pool
- New `_polygon_quote` (snapshot endpoint) and `_polygon_daily`
  (aggregates endpoint) in `services/market_data_pool.py`,
  reshaping Polygon's response to the same common quote/daily
  schema the other providers (Finnhub, AlphaVantage, TwelveData,
  Marketstack) emit. `source: "polygon"` tag on every row.
- `pool_config.get_market_data_provider_pool` auto-registers a
  `polygon-ab` entry when `POLYGON_API_KEY` is set in `.env`,
  default priority 4 (overridable via `MARKET_DATA_POLYGON_PRIORITY`).
- A/B mode: set priority=1 to make Polygon primary, others fail-over.
  Or leave at 4 to use Polygon only when other providers exhaust.
- Tests: 12 unit tests in `test_market_data_pool_polygon.py`
  (including HTTP mock transports for both endpoints).

### Pro Max (item #3)
- Already wired in a prior pass; verified live. Backend
  `/api/billing/checkout/subscription` accepts `pro_max` and
  `pro_max_annual`; Stripe live checkout URLs return for both.

### Regression
- Updated 2 existing patent_watch_api tests to be env-agnostic
  about `USPTO_API_KEY` (now a real key is in `.env`).
- 131/131 unit tests across all touched files pass.
- Testing agent reports 114/114 backend integration + frontend
  100% verified.

## 2026-02-08 (i) — Patent Watch seeded + 404 handling fix
- Seeded 11 watch queries via API: 7 by-assignee (OpenAI,
  Anthropic, DeepMind, Bridgewater, Renaissance, Two Sigma,
  Citadel) + 4 by-keyword (`adversarial`, `trading agent`,
  `options chain`, `multi-agent trading`).
- 179 USPTO filings cached on initial refresh; daily 4:15 cron
  will keep them fresh.
- Fix: `_fetch_from_uspto` now treats HTTP 404 from USPTO ODP
  as "zero matches" rather than an error condition. ODP
  returns 404 (instead of 200 + empty array) on no-match
  searches; the previous code surfaced this as `error="http_404"`
  in the UI, which would have been a false alarm. New behaviour:
  `error=null, fetched=0`. Test added (`test_fetch_from_uspto_404_means_zero_results_not_error`).
- 21/21 unit tests now pass.

## 2026-02-08 (h) — Patent Watch live activation + USPTO ODP schema fix
- Operator pasted real `USPTO_API_KEY` into `/app/backend/.env`
  and live USPTO fetches were wired up.
- Discovered the actual ODP Patent File Wrapper API uses a
  Lucene-string `q` parameter (not the JSON `_text_any` filter
  the legacy docs implied) and returns rows under
  `patentFileWrapperDataBag` with the title/applicant/inventor
  living under a nested `applicationMetaData` object.
- Rewrote `_build_query_payload` → returns Lucene `q` string,
  multi-word terms phrase-quoted, multi-condition OR'd.
- Rewrote `_normalise_row` → reads from `applicationMetaData`,
  prefers `earliestPublicationNumber` (US20260...A1) for the
  cache key + Google Patents URL, falls back to
  `applicationNumberText`. Legacy snake_case path kept for
  forward-compat.
- Tests refreshed against real ODP shape (20/20 still pass).
- Live verification: created query `assignee=OpenAI`, refresh
  returned 25 cached filings including "Systems and Methods for
  Image Generation with ML Models" (filed 2025-12-02), "Efficient
  Execution of Database Queries on Streaming Data", "Multi-task
  ASR System".

## 2026-02-08 (g) — Tech-debt refactor pass (P3 items A & B, COMPLETE)

### B. Split `AppContent` (frontend)
- `App.js` slimmed from 333 → 155 lines. AppContent now owns
  only modal state, view state, and the cross-component
  navigation bus.
- New `components/PreAuthRouter.jsx` (~115 lines): handles the
  three unauthenticated entry surfaces — `?demo=oauth` →
  AlpacaOAuthDemo, `/compliance/<broker>-oauth` →
  ComplianceOAuth, otherwise LandingPage + auth/waitlist/reset
  modals.
- New `components/AuthenticatedShell.jsx` (~145 lines): pure
  layout — Navbar + Ticker + AlertsPanel + main hub router +
  Footer + Chat + MobileNav + ModalManager.
- No behaviour change; landing page + admin shell render
  identically pre/post.

### A. Refactor `trading_bot_service.execute_signal()` (backend)
- 209-line monolith split into 5 cohesive helpers above the
  slim 95-line orchestrator:
    - `_check_kill_switch_and_drawdown(equity_curve)` (step 0)
    - `_compute_adjusted_size(...)` (steps 2 + 3a + 3b + 3c + 4)
    - `_resolve_qty(adjusted_size, signal, market_data)` (step 5)
    - `_fire_equity_shadow(synthetic_bot, signal, symbol, price)`
      (Research Shadow fire-and-forget block)
    - `_record_kill_switch_outcome(order)` (step 8)
- Public signature unchanged; every skip-reason string
  preserved; lazy `ai_core` imports preserved.
- Tests: 113/113 trading-bot tests pass (18 execute_signal_usd
  + 35 portfolio_risk_engine + 5 adaptive_sizing + 40
  drawdown_allocator + 15 kill_switch). 33/33 Patent Watch
  tests still pass — no cross-leg regressions.

## 2026-02-08 (f) — Patent Watch admin dashboard (P3 ready-to-schedule item C, COMPLETE)
- New backend service `services/patent_watch_service.py`: USPTO ODP
  client with X-API-KEY header support, query CRUD on
  `patent_watch_queries`, results cache on `patent_watch_results`
  (deduped on `(query_id, patent_number)`), graceful
  `missing_api_key` short-circuit when `USPTO_API_KEY` env var
  isn't set, refresh_all entry point for the daily scheduler.
- New admin routes under `/api/admin/patents/{queries, config,
  results, refresh/{id}}` (admin/owner only). `/config` reports
  `api_key_configured` boolean without ever leaking the key.
- New `PatentWatchPanel.jsx` admin UI tab: amber setup banner
  when key missing, add-query form, saved-queries list with
  per-row refresh + delete, results list with Google Patents
  deep-links. Wired into AdminPanel under the Insights group
  (`data-testid="admin-tab-patents"`).
- Daily APScheduler hook `_run_patent_watch_refresh` at 4:15
  every day; no-op when no queries exist.
- Tests: 33/33 passing (18 unit + 15 API integration). No
  regressions in the existing 166-pytest baseline.
- **Operator action to activate fetches:** set
  `USPTO_API_KEY=<your-key>` in `/app/backend/.env` (get one at
  https://data.uspto.gov/apis/getting-started — MyUSPTO account
  + ID.me linkage required) and restart backend.

## 2026-02-08 (e) — Backlog re-prioritization
- **Dropped:** Alpaca crypto LIVE execution wiring — per user
  ("Alpaca can get crossed off as well. Doesn't seem it's
  happening.") Removed from the parked section of ROADMAP.
- **Dropped:** Earlier-flagged manual items (duplicate Stripe
  webhook + landing-page Adversarial overclaim) per user
  ("we currently built it"). Stripe configuration left as-is.
- **Approved & promoted to P3 ready-to-schedule:**
  - Tech debt: refactor `trading_bot_service.execute_trade()`
  - Tech debt: split `AppContent.jsx`
  - Patent Watch admin dashboard (USPTO PatentsView API)
  - Tier 1 Visual Polish

## 2026-02-08 (d) — Tier 3 / Council Activation Playbook
- New ops doc: `/app/memory/TIER3_ACTIVATION_PLAYBOOK.md`.
- Single source of truth for the Adversarial → Council → Regime
  weight rollout. Covers env-flag inventory, phase progression
  (`shadow → risk_only → veto → full`), the `/api/admin/shadow/
  tier-readiness` payload, Council promotion thresholds, code-pinned
  bounds, rollback table, incident response, and Tier 3 firewall
  verification.
- Updates required whenever flag defaults / thresholds / phase rules
  change in code (see §3 and §5 of the playbook).

## 2026-02-08 (c) — Test-hygiene pass on `test_daily_digest.py`
- Refreshed 6 stale assertions to match the current codebase:
  * Digest data keys: `dark_pool`/`signals` → `smart_money`/`alerts`.
  * Prediction row keys: `ticker`/`verdict` → `symbol`/`direction`.
  * Greeting casing: `Good Morning` → `Good morning`.
  * DOCTYPE match: exact `<!DOCTYPE html>` → prefix `<!DOCTYPE html`.
  * Empty-state assertion: `"No recent data available"` → per-block
    hints (`"No high-conviction predictions"`, etc).
  * Scheduler log probe: old standalone banner → current consolidated
    `"Schedulers started: ... digest (6:00) ..."` line.
  * Trigger response: hardcoded `reason="no_api_key"` → shape check
    that accepts live-send and skip states.
- 19/19 `test_daily_digest.py` tests now pass. Full ML/digest/
  adaptation suite: 89/89 green.

## 2026-02-08 (b) — Daily ML-Health Digest Email (P1)
- New `services/ml_health_digest_service.py`:
  `collect_ml_health_data()` + `run_ml_health_digest()` orchestrator.
  Gathers 24h audit activity (auto/shadow soften + revert counts),
  top toxic-metric triggers, active adaptation roster, current
  thresholds, and a p25-based tuning hint (≥20 shadow obs).
- Scheduler wires `_run_ml_health_digest` at 08:00 UTC daily via
  APScheduler in `server._start_schedulers`.
- Admin endpoints:
  - `POST /api/admin/ml-health-digest/trigger` — manual fire
  - `GET  /api/admin/ml-health-digest/preview`  — render without sending
- Recipient defaults to `OWNER_EMAIL` (`admin@risedual.ai`);
  overridable via `ML_HEALTH_DIGEST_RECIPIENT` env.
- Idempotent per UTC date — second call returns
  `{sent: False, reason: "already_sent_today"}`.
- Tests: 5 new unit tests in `test_ml_health_digest.py`. End-to-end
  trigger verified — real email sent to admin@risedual.ai.

## 2026-02-08 — Safety-Rail Threshold Calibration (P2)
- `model_adaptation.get_auto_revert_config()` now reads thresholds
  from env (`ML_AUTO_REVERT_EFFECT_SIZE`, `..._EPSILON`,
  `..._MIN_COVERAGE`, `..._CONSECUTIVE_NEGATIVE`); defaults unchanged.
- New `GET /api/admin/adaptations/calibration` endpoint: analyses
  last N days of shadow/live audit rows, returns percentile
  distributions (`decision_score`, `decision_ratio`,
  `delta_r_trend`), per-metric roll-up, and a p25-based recommended
  effect_size once ≥20 observations are available.
- Admin UI `ModelAdaptationsPanel` gains a `CalibrationStrip` that
  renders the current vs suggested threshold, direction
  (tighten/loosen), and the exact env-var string to copy into
  backend `.env`. Silent until real observations exist.
- Tests: `test_adaptation_calibration.py` (4 tests, live backend)
  + 4 threshold-override tests in `test_auto_revert_safety_rail.py`.
  All 47 adaptation/ML tests passing.

## 2026-04-24 — Adaptation Hook Re-confirmed at the Correct Seam + Before/After Weight Telemetry
- **Caught a regression**: the `apply_adaptations_to_weights` call between `_severity_weights` output and `model.fit` got stomped by a subsequent search_replace in the same session. Only the `detect_and_create_adaptations` call at the top of the retrain had committed. Restored the one-line hook to where it belongs.
- **Integration seam** (exactly as prescribed):
  ```python
  sample_weight = _severity_weights(df)     # untouched
  # … regime + R-multiple weighting …       # untouched
  w, summary = await apply_adaptations_to_weights(db, X, w)  # ← only line that moves weights
  model.fit(X, y, sample_weight=w)          # untouched
  ```
- **Before/after telemetry added** to `log_row` (drift audit) AND to `logger.info` AND stamps onto the `retrain_adaptation_applied` activity event: `adaptation_mean_weight_before`, `adaptation_mean_weight_after`, `adaptation_weight_delta_mean`, full `adaptations_applied` summary.
- **Verified**: integration smoke test with 1 synthetic adaptation, 10-row synthetic X (5 match condition, 5 don't) → DRY-RUN mean unchanged at 1.0; ENABLED mean = 0.925 = (5×0.85 + 5×1.0) / 10, exactly the expected arithmetic. 11/11 toxic-spike tests pass.

## 2026-04-24 — Adaptation Engine Upgrade: Contrast Gate + Severity Ladder
Two statistical guardrails added on top of the existing bounds. Both caught REAL false-positive adaptations on first run against live data — concrete proof the gates were needed.

**1. Contrast gate** (`CONTRAST_MULTIPLIER = 1.25`)
Before creating an adaptation, compare `failure_rate(bucket) / failure_rate(global)` measured over the last 7 days of `features_snapshots`. Only proceed when the bucket fails at least 25% more often than baseline. Prevents penalizing useful-but-noisy signals.

**Live validation against current data**:
| Metric | Bucket rate | Global rate | Contrast | Decision |
|---|---|---|---|---|
| volume.liquidity | 28.9% | 26.8% | 1.08× | **BLOCKED** (marginal) |
| sector.momentum | 25.7% | 26.8% | 0.96× | **BLOCKED** (actually better) |
| macd.crossover | 27.8% | 26.8% | 1.04× | **BLOCKED** (marginal) |
| rsi.overbought | — | 26.8% | None | bucket=39 < 50 → **bypass**, evidence rules |

Without this gate, we would have blindly down-weighted volume.liquidity rows in retrain despite them failing only 7.8% more often than everything else.

**2. Severity ladder** — scales the adjustment factor by the mean absolute `return_1d` on failing rows in the bucket, matching the `_WEAK_THRESHOLD` / `_STRONG_THRESHOLD` vocabulary already used by severity-weighted retraining:
| Mean |return_1d| | Factor | Label |
|---|---|---|
| < 1% | 0.95 | mild |
| 1-3% | 0.85 | moderate |
| ≥ 3% | 0.75 | strong |

Forced-scenario tests verified each tier produces the expected factor.

**Bucket-size safety**: `MIN_BUCKET_SNAPSHOTS = 50`. Below this the rate comparison is too noisy to trust; the contrast gate is bypassed (evidence + cooldown still apply) and severity is best-effort.

**Latent bug fixed on the way**: `captured_at` on `features_snapshots` is stored as BSON `datetime`, not ISO string. Initial ISO-string `$gte` filter silently returned 0 rows — would have made every contrast check return None → silently bypass. Switched to native datetime object so Mongo does the tz-aware comparison correctly.

**UI**: `ModelAdaptationsPanel.jsx` surfaces the new stats — purple "1.08× baseline" contrast badge (with bucket/global rate tooltip) and amber "2.1% avg miss" severity badge. Admins see exactly why each adaptation was greenlit.

**Stored on each adaptation row**: `contrast`, `bucket_rate`, `global_rate`, `severity`, `bucket_snapshots`. Full forensic trail.

**Verified — 4-case gate suite + severity ladder + existing 9-case safety suite**:
- ✅ Contrast > 1.25 + severity=0.028 → creates with factor=0.85
- ✅ Contrast = 1.125 → **blocked**
- ✅ Small bucket (20 < 50) → bypass, creates with severity-derived factor=0.95
- ✅ Strong severity (0.055) → factor=0.75
- ✅ Real-data contrast across 5 metrics printed and matched expectations
- ✅ 11/11 regression tests pass, mypy 0, lint clean, webpack compiled

## 2026-04-24 — Prescriptive ML Adaptation (self-adapting retrain loop)
**The "what will change?" → "what changed?" loop closed.** Toxic alerts now *actually* reshape the next retrain.

**Design** — XGBoost doesn't take per-feature weights, so "reduce weight on low-volume breakouts by 15%" is faithfully implemented as *row-level* sample-weight down-adjustment on training rows that match the toxic pattern. The model learns less from those failure modes. Every knob is bounded.

**Safety guardrails** (pass this list if audited):
| Guard | Value | Purpose |
|---|---|---|
| `ML_ADAPTATION_ENABLED` env flag | default `false` | Full pipeline runs in dry-run until operator flips on |
| `MIN_EVIDENCE_COUNT` | 3 | No adapting on a single bad day |
| Factor bounds | `[0.7, 1.3]` hard clamp | Never more than ±30% per adaptation |
| `BASE_DOWN_WEIGHT` | 0.85 | Matches "15% reduction" narrative |
| `ADAPTATION_TTL_DAYS` | 14 | Mongo TTL auto-expires — no stale penalties |
| `COOLDOWN_DAYS` | 7 | Can't double-stack same metric |
| `MAX_ACTIVE_ADAPTATIONS` | 4 | Runaway protection |
| `MIN_CUMULATIVE_WEIGHT` | 0.1× baseline | Stacked multipliers can't nuke a row |

**New module `services/model_adaptation.py`**:
- `ADAPTATION_RULES` — 11 metric-key → (feature_column, condition, description) rules covering the full dotted namespace (`volume.liquidity`, `volume.spike`, `rsi.overbought`, `rsi.oversold`, `macd.crossover`, `sector.momentum`, `sentiment.negative`, and 4 pattern flags).
- `_FAILURE_CODE_TO_METRIC` — conservative 1:1 mapping from `FAILURE_MODES` codes to metrics so detection is predictable.
- `detect_and_create_adaptations()` — scans last 7 days of `alerts_sent`, creates bounded rows when evidence threshold clears, always narrates via `log_retrain_adaptation_planned`.
- `apply_adaptations_to_weights()` — multiplies `sample_weight` by active adaptation factors where rows match the rule's condition. Gated by env flag; returns summary for drift audit.
- `revert_adaptation()`, `disable_all_adaptations()` — full audit trail (no deletes).

**Retrain integration** (`services/ml_retrain_service.py`):
- `run_nightly_retrain` now calls `detect_and_create_adaptations()` at the start (plants "what will change?" narrative) and `apply_adaptations_to_weights()` right before `model.fit(X, y, sample_weight=w)`. Summary stamps into the training log under `adaptations_applied` + `adaptation_weight_delta_mean` for drift audit.
- Fixed a pre-existing corrupted duplicate `get_latest_model_info` block caught by the `ruff` syntax check while I was there.

**2 new activity events** (`agent_activity_service.py`):
- `retrain_adaptation_planned` 🧭 (info/warn) — "Next retrain will reduce weight on low-volume rows (volume_ratio < 0.8x) by 15%" (tells admins WHAT will change)
- `retrain_adaptation_applied` 🛠️ (warn/info) — "Applied 2 ML adaptations to retrain · 127 rows affected" OR "DRY-RUN: Would apply…" (tells them WHAT changed)

**3 new admin endpoints** (owner-gated):
- `GET /api/admin/adaptations` — list active + `enabled` flag state
- `POST /api/admin/adaptations/{id}/revert` — audit-preserving single revert
- `POST /api/admin/adaptations/disable_all` — nuclear switch

**New `ModelAdaptationsPanel.jsx`** in Admin → Developer Tools:
- APPLYING / DRY-RUN badge driven by backend `enabled` flag
- Amber "Dry-run mode" banner with exact env-flag instruction when off
- Per-adaptation card with metric, % change, evidence count, description, created/expires timestamps, one-click Revert
- "Disable all" kill switch with `window.confirm` gate

**Verified** — comprehensive 9-case safety suite:
- ✅ Low evidence (2 < 3) → no adaptation created
- ✅ Threshold met (3) → 1 adaptation created with correct factor 0.85
- ✅ Cooldown enforced → re-run creates 0
- ✅ Dry-run mode → weights untouched, summary still computed
- ✅ Real apply → correct rows down-weighted (0.85× where volume_ratio < 0.8)
- ✅ Out-of-band factor (0.2) → clamped to 0.7 floor
- ✅ Stacked multipliers (0.85 × 0.7 = 0.595) → above 0.1 floor, applied correctly
- ✅ Single revert works (flip to `active=false`, preserves audit)
- ✅ Kill switch deactivates all at once
- ✅ 3 HTTP endpoints return correct shapes (`enabled: false` confirms safe default)
- ✅ 11/11 toxic-spike regression tests pass, mypy 0, lint clean, webpack compiled

**Operator flip-on path**: `echo 'ML_ADAPTATION_ENABLED=true' >> /app/backend/.env && sudo supervisorctl restart backend`. The feed will start showing real-apply narrations (severity=warn instead of info) and the panel badge flips to APPLYING.

## 2026-04-24 — Batch Ship: Patent Pill + Systemic-Failure Escalation + Strategy Leaderboard
- **`BetaBanner.jsx`**: added the missed "Patent Pending" pill (hidden on `<sm`, tooltip reveals "U.S. Provisional Patent filed 04/23/2026 — App #64/047,926"). Closes the last-session user request for "all of the above" patent placements (Header/Footer/Tech Section/Banner).
- **Systemic-failure auto-escalation** (`agent_activity_service.py`, `routes/admin.py`):
  - New event type `alert_systemic_failure` 🆘 with `log_alert_systemic_failure` helper (error severity).
  - Fires automatically inside `POST /api/admin/alerts/replay` AFTER the regular replay event, *only* when `delivery_attempts >= 3` AND `still_failed` is non-empty.
  - Endpoint response adds `"systemic_failure": bool` so the frontend can surface a "needs human" banner on the matching audit row.
  - Verified: Case A (2→3 attempts, persistent failure) → both `alert_replay` (warn) + `alert_systemic_failure` (error) fire ✅. Case B (1→2 attempts, failing) → no escalation ✅. Case C (2→3 attempts, recovered) → no escalation ✅.
- **Strategy Leaderboard** (`GET /api/admin/strategies/leaderboard?days=…`, `StrategyLeaderboardPanel.jsx`):
  - Rolls up `learning_engine_trades` by strategy, coalescing the dual-schema `strategy` (newer agents) and `strategy_id` (older rows) into one group key (rows missing both → `(untagged)`).
  - Per strategy: trades, wins, losses, pending, win_rate, avg_r (resolved only), total_pnl (resolved only), last_trade_at.
  - Sorted by total_pnl desc; window selector (7/30/90/365 days); `🏆 #1` badge on the leader when there's a meaningful winner.
  - "Data warming up" banner when all trades in the window are still pending (current state: 51 pending across `near_52w_high`/`rsi_overbought`/`mean_reversion`, 0 resolved).
  - Owner-gated (reveals agent performance).
  - Verified via curl: 3 strategies surfaced correctly, dual-schema coalesce works.
- **Checks**: 11/11 toxic-spike tests pass, mypy 0 on all touched files, lint clean, webpack compiled successfully.

## 2026-04-24 — Dotted-Namespace Metric Keys
- **`routes/admin._extract_drivers`**: refactored canonical dedup keys from flat strings (`volume`, `macd`, `pattern`) to dotted namespaces (`volume.liquidity`, `volume.spike`, `macd.crossover`, `pattern.bull_flag`, `pattern.rsi_divergence`, `pattern.head_and_shoulders`, `pattern.bearish_engulfing`, `rsi.overbought`, `rsi.oversold`, `sector.momentum`, `sentiment.negative`, `liquidity.slippage`, `trend.exhaustion`, `macro.regime`).
- **Behavior preserved + clarified**: same-semantic signals still dedup (e.g. LIQUIDITY_GAP "low liquidity" vs fallback "low volume" both tag `volume.liquidity` → higher-weight wins). Opposite-semantic signals now coexist cleanly by design (`volume.liquidity` ≠ `volume.spike`, different pattern flags each get their own key). Unit suite verifies.

## 2026-04-24 — Failure-Code-Aware Drivers + Weighted Ranking + Metric Dedup
- **`routes/admin._extract_drivers`** rewritten as a two-layer engine:
  1. **Failure-code specific (HIGH signal)** — maps to our canonical `FAILURE_MODES` vocab: `TECH_FAKEOUT` (bull flag broke down, bearish momentum reversal), `LIQUIDITY_GAP` (low liquidity, slippage/spread expansion), `REGIME_SHIFT` (overbought RSI, trend exhaustion — covers "overextension"), `MACRO_SHOCK` (negative sector momentum, macro regime misalignment).
  2. **Fallback heuristics (MEDIUM signal)** fill remaining slots when the failure-code layer matched fewer than 3 drivers.
- **Weighted ranking**: each driver carries a weight (0.95 failure-code / 0.55–0.6 fallback). Final output sorted desc, so the strongest cause always shows first — UI implicitly communicates importance.
- **Metric-key dedup**: each driver tags its underlying metric (`rsi`, `volume`, `macd`, `sector`, `sentiment`, `pattern_*`). When the failure-code layer and fallback both speak to the same metric (e.g. "low liquidity (0.40x volume)" vs "low volume (0.40x)"), the higher-weighted phrasing wins and the redundant one is dropped. No more "overbought RSI (80)" appearing twice.
- **Frontend** (`AgentActivityFeed.SpikeDetailsBlock`): drivers now render as a `<ul>` with yellow disc markers instead of chip badges — reads like analysis ("· overbought RSI (82) · trend exhaustion · negative sector") rather than metadata tags.
- **Verified unit suite** across all 5 failure codes + UNKNOWN + clean + empty + dedup edge case:
  - `TECH_FAKEOUT (bull flag + bearish MACD)` → `['bull flag broke down', 'bearish momentum reversal']`
  - `LIQUIDITY_GAP (low volume)` → `['low liquidity (0.40x volume)', 'slippage / spread expansion']` (dedup killed "low volume" fallback)
  - `REGIME_SHIFT (overbought)` → `['overbought RSI (82)', 'trend exhaustion']`
  - `MACRO_SHOCK` → sector + macro + sentiment (distinct metrics, all kept)
  - `UNKNOWN (multi-signal)` → fallback produces `['overbought RSI (75)', 'MACD bearish crossover', 'low volume (0.50x)']`
  - Clean/empty inputs → `[]`
  - 11/11 toxic-spike tests pass, mypy 0, lint clean, webpack compiled successfully.

## 2026-04-24 — "Why" Drilldown Endpoint: Feature-Level Drivers from Real Snapshots
- **Schema reality check**: user's proposed endpoint targeted `learning_engine_trades` with `features_snapshot` + `regime` fields. Actual schema: `learning_engine_trades` uses `asset`/`strategy_id` (not `symbol`/`strategy`), has **no feature fields**, and zero rows with `r_multiple ≤ -1` in current data. Features live in `features_snapshots` (276k rows) which has `rsi_14`, `volume_ratio`, `macd`/`macd_signal`, `sector_momentum`, `sentiment_score`, `regime_label`, and 7 `pattern_*` boolean flags. The endpoint was adapted accordingly.
- **New endpoint `GET /api/admin/alerts/why/{alert_id}`** (`routes/admin.py`): reads `replay_payload.spike_details` off the alert (falls back to `affected_tickers` for legacy rows), fetches most-recent `features_snapshots` row per ticker (best-effort proxy — only 25/276k snapshots carry `prediction_id`, so exact-snapshot join isn't reliable), runs heuristic driver extraction via the new `_extract_drivers()`. Returns `{symbol, confidence, failure_code, date, regime, snapshot_at, drivers}` per ticker. Admin-gated.
- **`_extract_drivers()` heuristic** — 3 bullets max: overbought/oversold RSI (thresholds 70/30), low/surge volume_ratio (0.8 / 2.0), negative sector momentum (< -2%), MACD bearish crossover (macd<signal and macd<0), pattern flags (`pattern_rsi_divergence`, `pattern_head_and_shoulders`, `pattern_bearish_engulfing`, `pattern_double_bottom`), negative sentiment (< -0.3). Returns `[]` cleanly when no triggers fire or feature values are null.
- **`AgentActivityFeed.SpikeDetailsBlock`** upgraded to two-tier data: inline `spike_details` renders immediately when the drilldown opens, and an async fetch to `/api/admin/alerts/why/{id}` enriches the rows with drivers + regime by symbol merge. Drivers render as yellow chip badges; regime renders as a cyan badge next to the failure_code. Graceful degradation: fetch failure leaves the inline-only version intact (no broken UI).
- **Verified E2E** via curl:
  - Seeded alert with AAPL/NVDA/MSFT spike_details → endpoint returned 3 items with correct merge of spike metadata + latest snapshot timestamps ✅
  - `_extract_drivers()` unit-tested with toxic-signal scenario → `['overbought RSI (75)', 'low volume confirmation (0.60x)', 'negative sector momentum (-3.5%)']` ✅
  - Clean/empty inputs → `[]` (safe degrade) ✅
  - 404 for bogus alert_id (from earlier endpoint wiring) ✅
  - 11/11 toxic-spike tests pass, mypy 0, lint clean, webpack compiled successfully.

## 2026-04-24 — "Why Did This Alert Fire?" Drilldown + Latent Import Bug Caught
- **Latent runtime bug fixed**: `AgentActivityFeed.jsx` was using `RotateCcw`, `Loader2`, and `toast` without importing them. Webpack compiled fine (no static checker) but the failed-delivery replay button would have thrown `ReferenceError` at runtime the first time a user saw it. Added the full import set.
- **`services/agent_activity_service.log_alert_reserved`**: new optional `spike_details` arg — caller passes a pre-trimmed list of top offenders; persisted verbatim in the event metadata.
- **`services/market_memory_service._send_toxic_alerts`**: sorts `toxic_details` by confidence descending, passes top 5 to `log_alert_reserved` as `spike_details` (each row carries `symbol`, `confidence`, `date`, `failure_code`). Highest-conf misses surface first — the "model was most sure AND most wrong" cohort, the most teachable.
- **`AgentActivityFeed.jsx`**:
  - New `SpikeDetailsBlock` component rendered inside `alert_reserved` rows on demand. Shows symbol · confidence% · failure_code badge · date per row, plus a plain-English description of the failure mode.
  - Inline "Why did this fire? (N)" toggle button (using `HelpCircle` icon) on `alert_reserved` rows that have `spike_details`. Click → expands the drilldown; click again → hides.
  - `FAILURE_MODE_DESCRIPTIONS` mirrored from backend `post_mortem_service.FAILURE_MODES` (5 codes: TECH_FAKEOUT, MACRO_SHOCK, LIQUIDITY_GAP, REGIME_SHIFT, UNKNOWN).
- **Verified E2E** via probe with 6 fake toxic details (varied failure codes, descending confidence):
  - `spike_details` trimmed to top 5 (TSLA @ 75% cut, correct) ✅
  - Sorted desc: NVDA 92% → META 81% ✅
  - All 5 failure codes render with their human descriptions ✅
  - `fetch_recent` returns event with icon + full metadata shape the frontend expects ✅
  - 11/11 toxic-spike tests pass, mypy 0, lint clean, webpack compiled successfully.

## 2026-04-24 — Alerts Wired Into Agent Activity Feed (+ Filter Chips + Inline Replay + Toasts)
- **`services/agent_activity_service.py`**: added 4 event types to the controlled vocabulary — `alert_reserved` 🚨, `alert_suppressed` ⏭️, `alert_delivery` 📬, `alert_replay` 🔁 — plus matching `log_alert_*` convenience helpers. Severity mapping: reserved=warn, suppressed=info, delivery=error-if-any-failed-else-success, replay=success-if-clean-else-warn. Metadata carries `alert_id`, `run_id`, `delivered`, `failed`, `delivery_attempts` so the feed row can render inline actions.
- **`services/market_memory_service._send_toxic_alerts`**: emits `alert_reserved` on successful reserve, `alert_suppressed` on `DuplicateKeyError`, `alert_delivery` after per-recipient outcomes are stamped. All three calls are wrapped in `try/except: pass` per the "never break trading flow" contract even though `log_event` is already never-raise.
- **`routes/admin.alerts_replay`**: emits `alert_replay` after the replay completes. Carries `replayed`, `still_failed`, and the post-increment `delivery_attempts` counter.
- **`AgentActivityFeed.jsx`**:
  - **Filter chips** (All / Trades / Alerts / ML) above the list — startsWith-based mapping so new `alert_*` / `paper_trade_*` / `retrain_*` variants fold in with zero wiring.
  - **Inline "Replay failed (N)" button** on `alert_delivery` rows that have failures. Click → `POST /api/admin/alerts/replay`, optimistically refetches the feed so the new `alert_replay` event shows up without waiting for the 10s poll. Sonner toast on success/partial/error ("Replay delivered to 2 recipients · attempt #2" etc).
- **`AlertAuditPanel.jsx`**: added sonner toasts to the existing Replay button so every click has audible feedback, not just the inline text banner.
- **Verified E2E** via Python probe with monkey-patched `send_toxic_spikes_email`:
  - Fake `_send_toxic_alerts` with 1 OK / 1 FAIL → feed shows `alert_reserved` (warn) + `alert_delivery` (error, "1 sent, 1 failed") ✅
  - `POST /api/admin/alerts/replay` → feed gains `alert_replay` (success, "1 recovered"), row flips to `email_failed=false`, `delivery_attempts: 1→2` ✅
  - Two same-day `_send_toxic_alerts` calls → feed shows `alert_reserved` then `alert_suppressed` (duplicate reservation) ✅
  - 11/11 toxic-spike tests pass, mypy 0, lint clean, webpack compiled successfully.

## 2026-04-24 — Replay Failed Delivery + delivery_attempts (closes the recovery loop)
- **`services/market_memory_service._send_toxic_alerts`**:
  - **Bug fix**: `send_toxic_spikes_email` swallows its own exceptions and returns `bool`; the previous try/except-based detection never fired, so failures silently counted as successes. Now branches on return value.
  - **Reserve-time stamp** now carries `delivery_attempts: 1` and a complete `replay_payload` (`toxic_count`, `obsolete_count`, `total_before`, `total_after`, full `spike_details`, `persistence_tag`) so replays have full email fidelity without rerunning the cleanup scan.
- **New endpoint `POST /api/admin/alerts/replay?alert_id=…`** (`routes/admin.py`): reads `replay_payload` off the row, re-sends to `email_failed_recipients` only (never to already-delivered addresses — no double-send). Atomically `$inc`s `delivery_attempts`, stamps `email_replayed_at`, merges successful replays into `email_recipients`, and updates `email_failed` / `email_failed_recipients` to the post-replay state. Returns `{replayed, still_failed, delivery_attempts}`. Admin-gated. Rejects legacy rows without `replay_payload` with 409 rather than sending a degraded email.
- **`AlertAuditPanel.jsx`**: "Replay Failed" button on rows with failures, cyan "attempt #N" badge when `delivery_attempts > 1`, `email_replayed_at` timestamp in the expanded row, per-call status banner (green on full recovery, amber on partial, rose on error). Disabled with "legacy row — no replay payload stored" hint when the row pre-dates replay support.
- **Verified end-to-end** via curl:
  - Reserve + seed failed recipient → `POST /api/admin/alerts/replay` → `replayed=[admin@risedual.ai]`, `still_failed=[]`, `delivery_attempts=1→2`, row flipped to `email_failed=false` ✅
  - Idempotent re-run → `status: "no_failed_recipients"` ✅
  - Bogus alert_id → 404 ✅
  - 11/11 toxic-spike tests still pass, mypy 0, lint clean, webpack compiled successfully.

## 2026-04-24 — Email-Failure Flag + Alert Audit Tile
- **`services/market_memory_service._send_toxic_alerts`**: per-recipient delivery tracking. Instead of one try/except around the whole recipient loop, each `send_toxic_spikes_email` call is now individually guarded. After the loop, the reserved `alerts_sent` row is updated with `metadata.email_recipients` (succeeded), `metadata.email_failed` (bool), and `metadata.email_failed_recipients` (list of `{email, error}`). Closes the "reserved but nobody got the email" silent-drop failure mode the user flagged.
- **New endpoint `GET /api/admin/alerts/audit`** (`routes/admin.py`): returns the last N `alerts_sent` rows with `alert_id`, `run_id`, `date_bucket`, `toxic_count`, `affected_tickers[:10]`, `persistence_run`, `email_recipients`, `email_failed`, `email_failed_recipients`. Admin-gated (not owner-only — lower-tier admins also triage alerts). Optional `alert_type` filter, `limit` clamped 1–200.
- **New component `AlertAuditPanel.jsx`** wired into Admin → Developer Tools. Collapsible rows per alert with expand-on-click to inspect full `alert_id`, `run_id`, affected tickers, delivery outcome per recipient. Failed deliveries highlighted in rose. Refresh button. `data-testid` coverage on all interactive elements.
- **Verified**: probe script reserved a test alert, stamped a simulated partial-delivery failure (1 success, 1 timeout), and confirmed the endpoint returns the row with correct shape. Duplicate reserve still rejected by unique index. Webpack compiled successfully. 11/11 toxic-spike tests pass. Lint + mypy clean.

## 2026-04-24 — Toxic-Spike Dedup: Race-Condition Hardened (reserve-first)
- **Follow-up to same-day fix**: closed the read-then-write race window. Two concurrent cleanup runs could both pass `should_send_alert` before either wrote `record_alert`, producing ghost duplicates.
- **`services/alert_dedup.py`**: `alert_id` index migrated to `unique=True` (legacy non-unique `alert_id_1` is auto-dropped in `ensure_indexes` before the unique create — safe re-run). `record_alert` now propagates `DuplicateKeyError` while still swallowing other Mongo hiccups.
- **`services/market_memory_service._send_toxic_alerts`**: flipped to reserve-first pattern — `record_alert` is called BEFORE email/notifications. On `DuplicateKeyError` the flow suppresses silently. `should_send_alert` is no longer called on this path (the unique-index insert IS the gate). Added `run_id` (UTC ISO timestamp) to alert + notification metadata for forensic tracing.
- **Verified**: concurrent probe with 5 async reserves of the same `alert_id` → `oks=1 dupes=4`. Unique index confirmed live after boot. 11/11 toxic-spike tests still pass. Lint + mypy clean.

## 2026-04-24 — Toxic-Spike Dedup Bug Fix (repeat emails leaked)
- **Bug**: Admin received back-to-back toxic-spike emails 35s apart on 2026-04-10 and 2026-04-19. Root cause: dedup key was built from the exact affected-ticker set, so two cleanup runs seconds apart that produced slightly different toxic lists (e.g. `{MSFT, AAPL}` vs `{MSFT, AAPL, TEST_FAIL_61}`) hashed to different `alert_id`s and both passed the 48h suppress gate.
- **Fix in `services/market_memory_service._send_toxic_alerts`**: dedup key decoupled from the ticker set — now uses stable daily bucket `["toxic_spike_daily"]` so the 48h window collapses any same-day rerun to a single email. Real affected tickers are preserved in `metadata.affected_tickers` for audit. `record_alert` updated to write under the same dedup key so `persistence_run_count` keeps working.
- **Verified**: `tests/test_iteration59_toxic_spikes_alert.py` (11 passed); live probe showed run-1 sends, run-2 (same day, different ticker set) suppressed. Lint + mypy clean.


## February 2026 — Beta Signup Flow: Pro Access + 30k Credits for First 50
- **New `routes/beta.py`**: `POST /api/beta/signup`, `GET /api/beta/stats`, `GET /api/beta/recent`, `GET /api/beta/admin/list`. Cohort hard-capped at 50 (`BETA_SEAT_CAP` env). Entitlements per joiner: Pro subscription, 30,000 credits, 30-day trial, founding_member badge.
- **Dual-path signup**:
  * New email → generates `BETA-XXXX-XXXX` key, seeds a `waitlist` row (`cohort: first_50`, `beta_credit_grant: 30000`), returns key to UI.
  * Existing registered user → upgraded in-place (Pro + 30k credits), guarded against double-grant via `beta_cohort_granted_at` marker.
- **`services/credit_service.grant_custom_credits()`** helper: stamps `plan_key=pro` on the wallet, logs event, idempotency guarded by caller.
- **`routes/auth.redeem_beta_key` extended**: reads `beta_credit_grant` off the waitlist row, calls `grant_custom_credits`, marks `beta_signups.entitlements_granted=true`, returns `credits_granted` in the response.
- **Social-proof banner**: `BetaBanner.jsx` rotates between default copy and `"🎉 {name} just claimed seat #{n} — Pro + 30k credits for the First 50"` when a recent joiner exists. Polls every 60s.
- **`BetaSignupModal.jsx`**: entitlements checklist, live seat counter, copyable beta-key block on success, one-click "Redeem Now" button that opens the Auth modal's beta-key tab with the key pre-filled (via new `initialBetaKey` prop on `AuthModal` + `useModals.initialBetaKey` state).
- **E2E verified**: signup → key → redeem → Pro account with 30,000 credits confirmed via `/api/credits/balance`. Idempotency, honeypot bot trap, invalid-email rejection, cap-reached 409, existing-user in-place upgrade all tested.

## February 2026 — R-Weighted ML Retrain Wiring + Admin Tile (P1 + P2)
- **`services/ml_retrain_service._severity_weights`** now blends R-based weights over magnitude-based weights. Rows with `schema_version >= 4` and full execution data (entry/exit/stop/direction) route through `compute_sample_weight_from_trade`; `|R| < 0.25` → weight 0 (XGBoost dropped-from-gradient); legacy rows stay on the magnitude path. R-weight cap (2.5) matches magnitude cap → downstream 10× anti-explosion clip stays untriggered across either pipeline.
- **New helper `_r_eligible_mask_and_weights(df)`** in `ml_retrain_service.py` — vectorised eligibility check + per-row R-weight computation.
- **Drift logging**: every retrain log now stamps `r_eligible_frac` and `r_skipped_frac` so R-adoption coverage and noise-floor drops are visible per run.
- **New admin tile: `RDistributionCard`** in `MLHealthStrip.jsx` — pulls from `/api/admin/learning-engine/summary`, renders mean R + strong-R fraction with tone-aware coloring (healthy/drift/flat). Grid extended to 5 columns.
- **Tests**: 12 new (`test_ml_retrain_r_weighting.py`) covering eligibility mask, schema-version gating, invalid-direction rejection, LONG/SHORT weight symmetry, noise-floor zero-weight, weight-cap parity with magnitude path, and mixed-batch blending. Full ML/R suite **120/120 pass**. **mypy gate 0/0**.
- **Verified**: `scripts/backfill_snapshot_execution.py --dry-run` runs clean (0 resolved trades today; will populate via live resolve path). Frontend compiled successfully.
- **Skipped by design**: paper_trading_service manual-SELL enrichment — manual UI trades have no `prediction_id` linking them to `features_snapshots`, so there's no target row to enrich. The `prediction_tracker` wiring covers the actual ML training surface.

## February 2026 — features_snapshots Schema Extension for R-Weighted Retrain (P1)
- **`FeaturesSnapshot` schema extended** (`risedual_core/schemas/market.py`) with 4 optional execution-economics fields: `entry_price`, `exit_price`, `stop_loss`, `direction`. Schema version bumps to `4` on rows that carry the execution block. Backward-compatible — all default None.
- **New: `services/snapshot_enricher.py`** → `stamp_execution_on_snapshot(db, prediction_id, entry_price, exit_price, stop_loss, direction)`. Contracts: never-raise sidecar; only non-None fields written (no field-wipe); direction normalised to LONG/SHORT (invalid values dropped); non-numeric inputs silently dropped; requires non-empty `prediction_id` to avoid broad-match updates.
- **Live wire-in at `services/prediction_tracker.py`** resolve-pending site: when a prediction closes → stamps the 4 fields onto the matching `features_snapshots` row right after the `learning_engine_trades` r_multiple update. All values (entry, exit, stop, direction) already in scope → zero extra DB reads.
- **Backfill script**: `/app/backend/scripts/backfill_snapshot_execution.py` (dry-run + `--limit` flag). Walks resolved `learning_engine_trades`, reverse-looks-up prediction_id via `(symbol, direction, user_id)` + timestamp match, and stamps via the same canonical helper.
- **Tests**: 8 new in `test_snapshot_enricher.py` (happy path, prediction_id guard, partial-data, no-op short-circuit, invalid direction, non-numeric prices, Mongo failure isolation, no-match return). Full ML/R suite **95/95 pass**. **mypy gate still 0/0.**
- **Status**: Schema + writer + backfill live. R-weighted ML retrain integration (`compute_sample_weight_from_trade` + `should_skip_row_by_r` in `ml_retrain_service.py`) is the final piece.

## February 2026 — R-Weighting Noise-Floor Row Filter
- **New: `should_skip_row_by_r(r) -> bool`** in `ai_core/risk_weighting.py`. Returns True when `|R| < _R_NOISE_FLOOR (0.25)` — row should be hard-dropped from training (stricter than the 0.5 down-weight tier). Rationale: below 0.25R the exit was effectively at entry — trader fingers / slippage / data glitches, not trainable signal. Conservative on NaN/non-numeric (skip).
- **Composes with the piecewise tier system**: floor drops trash, `r_multiple_to_weight` 0.5-tier down-weights weak signal, ramp up-weights strong signal. Pinned via `test_skip_floor_composes_with_tier_mapping`.
- **Tests**: 7 new cases covering floor constant ordering vs threshold, below-floor skip, strict `<` boundary at 0.25 (≥0.25 kept), above-floor keep, NaN skip, non-numeric skip, tier composition. Full suite **50/50 pass**; mypy gate **0/0**.
- **Status**: Still unwired — awaits retrain loop integration alongside `compute_sample_weight_from_trade`.

## February 2026 — R-Distribution Wired into LearningEngine Admin Summary
- **`ai_core/learning_engine.py` → `get_summary()`** now includes an `r_distribution: {mean_r, strong_r_frac}` block, sourced from the most recent 500 resolved trades via `summarize_r_distribution` (from `ai_core.risk_weighting`).
  - Reads precomputed `r_multiple` already stamped by `prediction_tracker` resolve path — no schema migration needed.
  - Resolved-only filter (`status ∈ {win, loss}`) prevents pending trades (r_multiple=None) from poisoning aggregates.
  - Never-raise sidecar contract: DB failures return zero-stats, never 500 the admin endpoint.
  - Rounded to 4 decimals for clean JSON and stable UI diffs.
- **New: `/app/backend/tests/test_learning_engine_r_distribution.py`** — 5 tests covering empty-state shape, resolved-only query contract, None-row filtering, 4-decimal rounding, and DB-failure isolation.
- **Live-verified**: `GET /api/admin/learning-engine/summary` returns the new block (owner-only, `admin@risedual.ai`).
- **Status**: First consumer of `risk_weighting.summarize_r_distribution` is live. ML retrain integration still BLOCKED on full `features_snapshots` schema extension (entry/exit/stop/direction).

## February 2026 — R-Multiple Risk Weighting Test Coverage (P0)
- **New: `/app/backend/tests/test_risk_weighting.py`** — 39 tests covering the unwired `ai_core/risk_weighting.py` module: core R math (LONG/SHORT, sign, case-insensitivity, unknown→LONG default), edge cases (None/NaN/non-numeric/zero-risk/integer), piecewise tier mapping (noise/weak/ramp/strong cap), `_LOSS_AMPLIFIER=1.25` cross-module identity pin with `learning_upgrade`, loss penalty (strict `<0` boundary), end-to-end composition, long/short symmetry, max-weight ceiling matches magnitude path (2.5), drift summaries (empty/NaN/all-NaN).
- **mypy**: `risk_weighting.py` passes with 0 issues; gate on `services/` still 0/0.
- **pytest**: 39/39 pass. Full ML weighting family (learning_upgrade + signal_model + risk_weighting) 76/76 pass.
- **Status**: Module is now test-verified but still unwired — awaits `features_snapshots` schema extension (entry_price, exit_price, stop_loss, direction) before ML retrain integration.

## April 12, 2026 — Code Quality Sweep (P0/P1)
- **Security: MD5 → SHA-256** in `routes/accuracy.py`, `services/market_memory_service.py`, `services/post_mortem_service.py`
- **Security: Hardcoded test credentials centralized** — 25 test files updated to import from `conftest_creds.py`
- **React: Index-as-key anti-pattern fixed** in 9 components (WarRoomCards, OrderFlowHeatmap, MemoryDashboard, DarkPoolData, PaperTrading, PredictionCards, OrderFlowPanel, WhaleRadar, HypothesisResults)
- **Backend refactoring:** `broker.py` oauth_callback split into 4 helpers; `accuracy.py` classify_failure extracted ChromaDB helper
- **Confirmed: eval()/exec() already removed** by previous agent — safe AST evaluator in backtester_service.py
- **Verified:** Iteration 86 — 100% pass (15/15 backend, all frontend)

## April 12, 2026 — Security Audit Dashboard + Component Splitting
- **New Feature: Security Audit Dashboard** in Admin Panel (new "Security" tab)
  - Backend: 5 endpoints under `/api/admin/security/` (overview, failed-logins, oauth-rotations, broker-connections, unlock)
  - Frontend: Stat cards + expandable sections showing real security data
- **Admin Panel access fixed** for `admin` role (was owner-only in Navbar + App.js)
- **Navbar refactored** — mobile menu extracted to `MobileMenu.jsx` (322 → 239 lines)
- **React Hooks: Zero warnings** — ESLint scan of 136 files with exhaustive-deps rule returned 0 issues
- **Verified:** Iteration 87 — 100% pass (20/20 backend, all frontend)


## April 11, 2026 — Media, Legal, Broker, OAuth, Voice
- Media Upload / Object Storage System (storage_service.py, MediaManager.jsx)
- Broker API Key Vault + Role-Based Execution
- 3-Legged OAuth + PKCE + Refresh Token Rotation
- Trade Execution Push Notifications
- Legal Pages (Terms, Privacy, Risk, Disclaimer)
- Voice Chat (TTS + STT via OpenAI)
- SEO Optimization (meta tags, structured data, sitemap)
- About Us Page
- Landing Page commercial video embed
- Real Polygon.io Dark Pool data integration
- Custom WhaleRadar + AdversarialHub UI

## April 10, 2026 — Core AI & Market Systems
- AI Sentiment Heatmap (multi-agent sector analysis)
- Market Vector Memory System (ChromaDB + MiniLM embeddings)
- Memory Training (2,973 historical episodes)
- Nightly Cleanup + Toxic Spikes Alerts
- Dual-Signal Adversarial AI (Edge vs Veto)
- Failure Mode Classification
- AI Post-Mortem Analysis
- Real-Time SSE Insight Stream
- Memory Dashboard UI
- Order Flow / Institutional Wall Detection
- Real-Time Order Flow Heatmap (Binance L2)
- VAPID Web Push Whale Alerts
- Multi-Ticker Whale Radar
- Portfolio Agent with AI Tool Calling
- Paper Trading System
- Historical Sentiment Tracking
- Enriched Regime Format (Fear & Greed + VIX)
