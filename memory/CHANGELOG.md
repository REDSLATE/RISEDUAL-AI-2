# RISEDUAL AI — Changelog

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
