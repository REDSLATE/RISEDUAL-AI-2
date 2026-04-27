# RISEDUAL AI — Changelog

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
