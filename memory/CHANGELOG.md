# RISEDUAL AI — Changelog

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
