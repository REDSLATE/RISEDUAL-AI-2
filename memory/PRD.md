# RISEDUAL AI — Product Requirements Document

## 1. Original Problem Statement
Build a functional clone of a trading app named **RISEDUAL AI** — a full AI-powered
adversarial trading platform with:
* real market data + functional broker connections (Alpaca, Kraken)
* Stripe subscription gateway + credit economy
* AI chat assistant + streaming agent loop
* AI market prediction engine (multi-model consensus)
* AI Strategy Builder + Strategy Backtester + Strategy Marketplace
* a unified AI War Room (Strategist vs. Auditor loop)
* Watchlist Intelligence, Sector Heatmap, real-time P&L, accuracy tracking
* Thread-Safe Native Multi-Agent Engine + Market Vector Memory System
* VAPID push notifications, Live Order Flow Heatmaps
* Paper trading wired to the AI chat
* SEO optimisation + custom domain deployment

## 2. Tech Stack
* Frontend: React · TailwindCSS · lucide-react · html2canvas · EventSource (SSE)
* Backend: FastAPI · Motor (async MongoDB) · APScheduler · emergentintegrations
* DB: MongoDB
* 3rd-party: OpenAI/Anthropic/Google via Emergent Universal Key · OpenRouter · Stripe · Kraken · Alpaca · OpenFIGI · SEC EDGAR · Resend · Finnhub · FRED · FMP · Alpha Vantage

## 3. What's Been Implemented (latest first)

### `_make_id` v2 Hardening — Corrupt prediction_id Defence (Feb 27, 2026)

The remaining v2 hardening from the previous session — closes
the `"v2|"` collision risk surfaced during the VWAP/CI-guard
review thread.

**The gap closed**: ``_make_id``'s v2 path was
``f"v2|{prediction_id}"`` after a single ``or ""`` falsy guard.
That handled ``None`` and empty string but missed:

* whitespace-only strings (``"   "``, ``"\t\n"``) — truthy →
  produced ``"v2|   "`` etc., which collided across rows by
  whitespace length
* ``float('nan')`` / ``float('inf')`` — truthy → produced
  ``"v2|nan"`` / ``"v2|inf"`` — every NaN-id row collapses to
  one ChromaDB row, silently overwriting earlier verified
  predictions
* sentinel strings ``"nan"`` / ``"none"`` / ``"null"`` /
  ``"undefined"`` / ``"inf"`` — usually the result of
  ``str(None)`` or ``str(float('nan'))`` higher up the stack;
  silently collapsed every "stringified-None" row to a single id

**New helper** `services/market_memory_service._normalize_prediction_id`:
returns the cleaned string or ``""`` if invalid. Acceptance
contract pinned by tests:

* ``None`` / ``""`` / whitespace → ``""``
* NaN / Inf / -Inf → ``""``
* Sentinel strings (case-insensitive, post-strip) → ``""``
* Numeric (``12345``) → ``"12345"`` (legacy schema safety)
* Real ids (``" pred-123 "``) → ``"pred-123"`` (whitespace stripped)

When the helper returns ``""``, ``_make_id`` falls through to
the v1 ``(symbol, date, price)`` fallback path. v1 is itself
dedup-keyed on real fields so a corrupt-prediction-id row keeps
a meaningful id instead of clobbering its neighbours on
``"v2|"``.

**Test coverage**: 5 new regression tests in
`test_mongo_to_chroma_sync.py` covering the full corrupt-input
matrix (16 inputs collapsed into one parametrised loop), the
happy v2 path still wins over v1, whitespace stripping
correctness, distinct-id distinctness, and numeric-id
coercion. **109/109** green across the full sync test suite
(20 mongo↔chroma + 28 datetime_utils + CI guards + drift
detector + position reconciler + drift alert watcher +
prediction_date backfill). Lint clean.

### Backfill + VWAP Equity Reconciler + CI Slice Guard (Apr 30, 2026)

Three actionable backlog items shipped together. Webhook close
detection and per-leg spread fills remain deferred for the
documented reason (provider stories not stable enough to commit
to a contract).

**1. Nightly `prediction_date` backfill**

`services/prediction_date_backfill.py::backfill_prediction_date(db)`
hooks into the existing 2:00 UTC `_run_memory_cleanup` tick.

* Idempotent: query is `$or: [{$exists: False}, {$eq: None}]` so
  rows already populated never re-process.
* Bounded: 5,000 rows per pass — protects nightly cleanup against
  runaway backlogs.
* Self-disabling: residual count returned in the result so the
  operator can see "still 47 pending" without inspecting Mongo.
* Failure isolated: per-row exceptions counted as `skipped`, never
  raised.

**Live backfill: 228 rows filled, 0 skipped, 0 pending.** Drift
endpoint can now bucket on the native `prediction_date` field
without the `to_iso_date(timestamp)` fallback.

**2. Volume-weighted equity exit price**

Pre-fix `_is_equity_position_closed()` used only the *earliest*
opposite-side fill — a multi-leg unwind (100 shares closed in two
50-share fills) lost the second fill's price contribution. Now:
volume-weighted average across all matching fills since the open,
with cumulative qty capped at the original position size so phantom
fills (rare but seen with broker backend hiccups) can't poison
the average.

Defensive fallbacks:
* `filled_qty` preferred → `qty` → fall back to original position
  qty when broker omits both (preserves single-fill historic
  semantics).
* Under-reported broker data (sum of fills < open qty) computes
  VWAP over what's available — better than dropping the row.
* `close_fill_count` surfaced in the decision dict for downstream
  observability.

5 new tests pin: VWAP across 2 fills, cap at original qty, partial
under-reporting, `qty`-fallback when `filled_qty` missing, plus the
existing single-fill paths still work.

**3. CI guard against `[:10]` on Mongo doc accesses**

`tests/test_no_brittle_slice_on_mongo_dates.py` greps for
`<expr>.get("…", default)[:10]` and `(<expr> or "")[:10]` patterns
in `services/`, `routes/`, `ai_core/`. Strict by default; explicit
`ALLOWLIST` for known-safe sites (Marketstack ISO strings, list
slices on `bids`/`asks`, watchlist-form date strings).

**Real bugs the guard caught on first run:**
- `services/waitlist_service.py:273` — `entry.get("signed_up_at", "")[:10]`
  on Mongo `signed_up_at` field. Same exact bug shape as the
  Chroma sync sites; would silently drop signups from the daily
  chart on tz-naive round-trip. Routed through `to_iso_date`.
- `routes/accuracy.py:158` — `_update_chromadb_failure_code()` was
  hand-rolling its own SHA-256 with the historic
  `pred.get("timestamp", "")[:10]` slice. Identical bug to the one
  fixed in `post_mortem_service.py` two threads ago — the guard
  caught the duplicate. Now delegates to `_make_id()` so the
  v1/v2 contract stays unified across all callers.

**Tests**: 119/119 green across 9 sync-related suites.

### Drift Detector Honesty Fixes — Persisted Last-Rebuild + Date-Field Alignment (Apr 30, 2026)

Two operator-trust gaps in the drift endpoint, both surfaced by the
operator while inspecting the live response shape.

**1. `last_rebuild_at` now persists across restarts**

Pre-fix this lived only in `services/mongo_chroma_sync_metrics`'s
in-process state, so every backend hot-reload wiped it. The alert
watcher's "8% drift one hour after rebuild = actively broken"
reasoning was unreliable across deploys — operators couldn't trust
the timestamp they were seeing.

Now: `mark_rebuild()` is async-Mongo-backed. Single-document
collection `mongo_chroma_sync_state` (`_id: "mongo_chroma"`),
upserted on every rebuild. `get_last_rebuild()` reads from Mongo
on cold start, warms an in-process cache for subsequent reads.
Persistence is best-effort — a Mongo write failure logs at WARN
but never raises (the rebuild has already succeeded by the time we
write). Tests cover the cache-miss-falls-through-to-Mongo and
"backend restart" scenarios.

**2. Mongo query now aligned with rebuild's date-field semantics**

Pre-fix surfaced as: rebuild ran, processed 101 rows, dashboard
still showed `mongo_verified_count: 0`. Two compounding bugs:

* **Wrong window field.** Drift filtered on `prediction_date`;
  rebuild filtered on `verified_24h.verified_at`. Different rows.
* **Bucketing on a null field.** Even after fixing the window,
  `prediction_date` is `None` on older rows — the aggregation
  grouped them all under one null key that the dashboard then
  dropped. 101 verified predictions silently became 0.

Now: `_mongo_per_date_counts()` filters on
`verified_24h.verified_at >= cutoff_iso` (matches rebuild) and
buckets via `to_iso_date(timestamp)` for any row where
`prediction_date` is null. The Chroma side keys metadata on the
same `to_iso_date(timestamp)` so per-date totals are directly
comparable. Endpoint response now also surfaces
`window_field: "verified_24h.verified_at"` so the contract is
self-documenting.

**Live verified post-fix**:
```
mongo_verified_count: 101    # matches rebuild's "rebuilt: 101"
chroma_episode_count: 1027   # full training corpus
drift: -926                  # benign over-supply
recommendation: ok
last_rebuild_at: 2026-04-30T11:28:18+00:00   # survived hot-reload
```

**Tests**: 76/76 green across drift/alert/sync suites — added
4 new tests for persistence (write/read survives restart, Mongo
failure non-fatal, empty-state cold start) + 1 alignment test
(filter uses verified_at, buckets via timestamp through
to_iso_date).

### Post-Fix Rebuild + Drift Alert Watcher (Apr 30, 2026)

Two operator follow-ups from the Mongo→Chroma sync hardening thread.

**1. Post-fix rebuild executed against production state**

```
POST /api/accuracy/memory/rebuild-from-mongo?days=30&limit=5000
→ { "rebuilt": 101, "skipped": 0, "since": "2026-03-31T10:27:29Z" }
```

101 rows rebuilt, 0 skipped. ``last_rebuild_at`` now stamped;
drift detector shows ``drift_pct=0.0`` recommendation ``ok`` with
``drift=-379`` (benign training over-supply). The first post-fix
rebuild is now in the books.

**2. Drift alert watcher (5-minute cron)**

`services/drift_alert_watcher.py::check_and_alert()` — runs every
5 minutes through the existing APScheduler. Three firing rules,
all dedup-bucketed by UTC day via ``ai_core_alerts.emit`` so a
persistent condition fires ONE alert/day (not 288):

| Rule | Condition | Alert type |
|-|-|-|
| Threshold crossing | `drift_pct` crossed into `≥ 10%` band | `memory_drift_rebuild_recommended` |
| Sudden jump | `delta_pct > 5` between consecutive ticks | `memory_drift_jump` (bucketed per from→to pair) |
| Recovery | Drift dropped to `< 1%` after same-day rebuild alert | `memory_drift_recovered` |

Quiet path (99% of ticks): zero emits. The watcher wraps the
whole tick in `try/except` so a bad Mongo response can't crash
the scheduler thread. Failure returns `{ok: False, reason: "drift_compute_failed"}`.

**Threshold consistency pin**: `test_thresholds_match_drift_endpoint_classifier`
asserts `REBUILD_PCT == routes.admin_memory_drift.THRESHOLD_INVESTIGATE_PCT`
and `OK_PCT == THRESHOLD_OK_PCT` so the dashboard and the alert
channel can never disagree on what "rebuild recommended" means.

**Scheduler banner** now reads "… position reconciler (30m),
drift alert watcher (5m)" on startup.

**Tests**: 11/11 pass — 4 firing scenarios, 3 non-firing
scenarios (small jumps, drops, quiet path), dedup behaviour, and
the 2 constant-consistency pins.

### Patent J Drift Card + Tier 3 Detail Card + Multi-leg Spread Reconciler (Apr 30, 2026)

Three operator-grade visualizations and the deferred spread-close
work all landed together. Testing-agent verdict: backend 100%,
frontend 100%, no issues, no action items.

**1. Multi-leg options spread reconciliation**

- `services/position_reconciler.py::_is_spread_closed(legs, option_positions)`
  — conservative rule: spread is closed only when **every leg's OCC**
  is absent (or zero-qty) at the broker. One remaining leg = still
  partially in market = wait. False-positive guard preserved.
- `_reconcile_options_for_user` now branches on `is_spread`; the
  new spread path appends `OUTCOME_VERIFIED` with
  `asset_class="options_spread"`, sums leg qty, and uses the net
  debit/credit `limit_price` to compute conservative P&L.
  `outcome_leg_count` persisted on the row for downstream analytics.
- `routes/options_trading.py` spread route now runs the
  `manual_order_guard` BEFORE `_log_order_audit` so spread orders
  get a `proof_chain_entity_id` written into Mongo (was previously
  guard-less for spreads).
- 5 new pytest cases in `test_position_reconciler.py` covering open
  / all-closed / no-OCC / zero-qty / end-to-end spread sweep.

**2. Tier 3 progress detail card**

- `services/tier3_readiness.py::compute_tier3_breakdown(stats)` —
  returns 6 rows with `key/label/current/target/unit/progress_pct/
  weight_pct/earned_pts/met/hint`. Pinned property:
  `sum(earned_pts) == compute_tier3_score(stats)` within ±0.5.
  Live verified: composite=55.71 vs sum=55.72.
- Six gates: exposure (20pts), volume (15pts), high_conf_accuracy
  (25pts), risk_control (20pts), stability (10pts), canary (10pts).
- High-conf row: bar shows sample-fraction (the binding constraint)
  but earned_pts mirror composite formula `high_conf_wr * 25` so
  bars and headline never diverge.
- Field `tier3_breakdown` added to existing
  `/api/admin/shadow/tier-readiness` response — no new route.
- React component `Tier3ProgressDetailCard.jsx`: dark-slate panel
  matching BlocksPreventedCard, headline composite + 6 progress
  bars colored by met/threshold, blocker hints inline, next-steps
  list at bottom, polls every 30s.
- 15 new pytest cases in `test_tier3_breakdown.py` (gate shapes,
  weight sum=100, earned↔composite reconciliation, sample-size
  gating, threshold inversions, defensive missing-keys).

**3. Patent J memory drift card**

- React component `MemoryDriftCard.jsx`: dark-slate panel matching
  BlocksPreventedCard, displays `mongo_verified_count`,
  `chroma_episode_count`, `drift_pct` tiles + recommendation badge
  (ok/investigate/rebuild) + per-date skew table + skip counters
  + last-rebuild context. 7d/30d/90d window selector. Polls every
  60s.
- Mounted at the top of `ProofChainExplorer` (Patent J admin panel)
  because corrupted memory is the fastest way for downstream
  analytics to lie.

**Test coverage**: 89 backend tests green (15 reconciler + 15 tier3
breakdown + 16 drift detector + 28 datetime/sync + 15 other).
Lint clean across all 4 modified frontend files. Live endpoints
verified end-to-end via testing agent.

### Mongo→Chroma Drift Detector + Sync Hardening Tightenings (Apr 30, 2026)

Three operator-grade follow-ups landed against the sync work:

**1. `_make_id` v1 price canonicalization**

Mongo can hand back a financial field as ``Decimal('180.0')``,
``Decimal('180.00')``, ``180`` (int), ``180.0`` (float), or
``"180.00"`` (str) depending on which writer last touched the doc.
Pre-fix all five hashed differently → up to FIVE ChromaDB rows for
the same trade. Now coerced through ``f"{float(price):.4f}"`` so all
five canonicalize to ``"180.0000"``. Test
``test_make_id_v1_price_canonicalized_across_numeric_types`` pins
the contract.

**2. Structured sync skip counters**

The four sync sites previously had bare `except: skipped += 1`
that swallowed every failure into a void. Replaced with
``services/mongo_chroma_sync_metrics.record_skip(reason, doc_id, exc)``
which:
- Bumps an in-process counter keyed by reason
  (e.g. ``rebuild_save_failed``, ``warmup_save_failed``,
  ``post_mortem_chroma_update_failed``, ``verify_chroma_save_failed``)
- Logs at WARN with structured fields so the next regression shows
  up in observability instead of a screenshot four months later

**3. Drift detector endpoint**

``GET /api/admin/memory/drift?days=30&top_skew=10`` (owner-only).
Returns:
```
{
  available, window_days,
  mongo_verified_count, chroma_episode_count,
  drift,                  # mongo - chroma; positive = sync regression
  drift_pct,              # max(drift, 0) / mongo * 100
  recommendation,         # ok / investigate / rebuild
  thresholds: { ok_pct: 1.0, investigate_pct: 10.0 },
  by_date_top_skew,       # localizes regression window
  sync_skipped_total,     # in-process skip counters
  last_rebuild_at,
  last_rebuild_summary,   # { rebuilt, skipped, since }
  computed_at
}
```

Sign convention:
- **Positive drift** (mongo > chroma) = sync silently dropped rows
  → triggers the recommendation classifier.
- **Negative drift** (chroma > mongo) = benign over-supply from
  ``memory_training_service`` yfinance bulk-training → surfaced
  in the breakdown but never recommends rebuild. Per-date sort
  ranks positive (actionable) skews above negative ones.

``mark_rebuild()`` is now called by both
``/api/accuracy/memory/rebuild-from-mongo`` and the startup
``chroma_warmup`` so the operator can distinguish "8% drift right
after rebuild = expected" from "8% drift one hour after = actively
broken".

**Live verified**: endpoint returns real data; current state shows
0 verified Mongo predictions in the last 30 days vs 371 Chroma
episodes (the bulk-training ones), drift_pct = 0.0, recommendation
= "ok" — exactly as designed.

**Tests**: 69/69 green (3 new price-canonicalization, 9 threshold
parameterizations, 6 drift endpoint cases including negative-drift
benign handling and positive-skew ranking).

**Operator note**: The first post-fix rebuild is the one that
actually reconciles state. Run `POST /api/accuracy/memory/rebuild-from-mongo`
once after this deploy lands.

### Mongo→Chroma Sync Re-coercion Fix (Apr 30, 2026)

**The bug the user surfaced**: even after fixing the Mongo
tz-naive datetime math (`ensure_utc`), the *sync layer* that reads
from Mongo and pushes into ChromaDB could still re-coerce data
into broken shapes. Investigation found 4 sites all using the same
brittle pattern:

```python
"date": mongo_doc.get("timestamp", "")[:10]
```

Three failure modes, all silent (swallowed by broad `except`):

1. **Mongo round-trips `timestamp` as tz-naive datetime** → slicing a
   `datetime` raises `TypeError` → caller's `except` swallows →
   ChromaDB row never written → vector store silently drifts from
   MongoDB. (Same root cause as toxic-count under-reporting from
   2026-04-24.)
2. **Field missing/None** → `""[:10]` returns `""` → `_make_id`'s v1
   hash collides every dateless row to one ChromaDB id → newer
   episodes overwrite older ones, losing data.
3. **`datetime` value sneaks into ChromaDB metadata** — Chroma only
   accepts str/int/float/bool; passing a datetime is rejected
   inconsistently across versions.

**New helper** `services/datetime_utils.to_iso_date(value)`:
- Accepts `datetime`, `date`, ISO string (with/without `Z`/offset),
  bare `YYYY-MM-DD` prefix, and returns canonical `YYYY-MM-DD` or
  `None`.
- Strict: invalid calendar dates (`2026-13-99`), garbage strings,
  empty strings, and non-(date|str|None) types all return `None`
  rather than coercing to today.

**`save_regime` hardening** (`services/market_memory_service.py`):
- `metadata` build site now coerces `symbol`/`date`/`outcome`/
  `prediction_id`/`failure_code` to clean strings via `to_iso_date`
  + `str(...)` so a tz-naive datetime can never reach Chroma.
- Falls back to today's UTC date if `to_iso_date` returns `None`
  (was previously `regime.get("date", today)` which didn't fire
  on empty-string).

**`_make_id` hardening** — same coercion in the v1 fallback path so
the same logical episode hashes to the same ChromaDB id whether the
caller passed a datetime or a string. Test:
`test_save_regime_id_stable_for_str_vs_datetime_date` proves this
end-to-end.

**Fixed callsites**:
- `services/prediction_tracker.py:729` (verify_pending_predictions)
- `routes/accuracy.py:375` (`/memory/rebuild-from-mongo`)
- `server.py:533` (chroma_warmup at startup)
- `services/post_mortem_service.py:261` — was hand-rolling the doc
  id with the same brittle slice; now delegates to `_make_id` so the
  v1/v2 contract stays unified.

**Test coverage**: 37/37 new tests passing (28 datetime_utils
including 13 new `to_iso_date` cases + 9 sync hardening tests).
Critical proof test: `test_save_regime_id_stable_for_str_vs_datetime_date`
demonstrates same-logical-episode determinism.

### Position Reconciler — Step 10 OUTCOME_VERIFIED for External Broker Fills (Apr 30, 2026)

**The gap closed**: Crypto + smart orders had OUTCOME_VERIFIED on
close (the proof chain's "step 10"). Live equity (`broker.py`) and
options (`options_trading.py`) did not — fills happen externally,
the close happens days later, and there was no in-process callback.
The chain dead-ended at fill.

**New service `services/position_reconciler.py`**:
- `reconcile_equity_positions(db)` — sweeps `trade_orders` rows
  with `proof_chain_entity_id` set and `outcome_appended != True`,
  groups by `(user_id, broker_id)` to minimize broker API calls,
  pulls `client.get_positions()` + `client.get_orders(status="closed")`,
  and decides if each row is closed by:
  * Symbol absent from positions, AND
  * A later opposite-side filled order exists with a usable price.
  False-positive guard: if position is gone but no exit fill is
  found, the row is left for the next sweep — we never invent a
  price.
- `reconcile_options_positions(db)` — same shape, scoped to
  single-leg `option_orders` (multi-leg spread reconciliation
  deferred). Uses `adapter.list_option_positions()` when the
  configured options provider exposes it.
- `run_position_reconciler(db)` — single scheduler entry running
  both sweeps.

**Schema additions** (forward-compat, safe with existing rows):
- `trade_orders.proof_chain_entity_id` + `outcome_appended` +
  `outcome_appended_at` + `outcome_block_hash` + `outcome_exit_price`
  + `outcome_pnl`.
- `option_orders.*` — same suffix fields.

**Order-placement reordering**:
- `routes/broker.py` and `routes/options_trading.py` now run the
  manual-order guard *before* persisting the audit row so
  `proof_chain_entity_id` is written atomically. (Previously the
  guard ran after; the entity_id only flowed back to the response,
  never to Mongo.)

**New scheduler job**: `position_reconciler` runs every 30 minutes;
no-op when no eligible rows exist, bounded at 200 rows per pass
to keep broker API call volume predictable.

**New admin endpoints**:
- `GET /api/admin/position-reconciler/status` — pending +
  closed_30d counts for equity & options.
- `POST /api/admin/position-reconciler/run` — manual sweep
  trigger, rate-limited to once per 60s per owner.

**CI guard**: `tests/test_no_unguarded_mongo_datetime_math.py`
greps the codebase for `datetime.now(timezone.utc) - <var>` /
comparison patterns and fails CI if any new code uses a
Mongo-derived datetime in math without `ensure_utc()`. Strict by
default — explicit `ALLOWLIST` for known-safe in-process state
(provider router cooldowns, kill-switch tripped_at, etc.).

**Test coverage**: 34/34 tests passing (15 datetime_utils + 1 CI
guard + 10 reconciler unit + 8 API contract tests added by the
testing agent). End-to-end verified via cookie-based auth.

### Mongo Tz-Aware Datetime Sweep + `ensure_utc()` Helper (Apr 30, 2026)

**Problem**: MongoDB strips `tzinfo` from BSON Dates and truncates
sub-millisecond precision on round-trip. Doing
`datetime.now(timezone.utc) - mongo_dt` raises
`TypeError: can't subtract offset-naive and offset-aware datetimes`
which has historically been swallowed by broad `except` blocks and
silently disabled business logic (auto-promotion suggestions, OAuth
token refresh, trial expiry checks).

**Helper**: New `services/datetime_utils.py::ensure_utc(dt)` —
canonical guard that accepts a tz-aware datetime, a tz-naive datetime,
an ISO-8601 string (with or without `Z`), or `None`, and returns a
tz-aware UTC datetime or `None`. 15 unit tests in
`tests/test_datetime_utils.py` pin the contract.

**Fixes applied** (3 real bugs found in sweep):
- `routes/broker.py:215-222` — `oauth_expires_at` from Mongo was
  fed into `datetime.now(timezone.utc) - expires_at` without
  re-tagging UTC; OAuth token refresh would silently no-op when the
  field was a Mongo round-tripped naive datetime.
- `services/auth_helpers.py:67` — `is_pro_user()` only handled the
  string shape of `trial_expires_at`; a naive Mongo datetime would
  raise on `expires > datetime.now(timezone.utc)` and crash any
  Pro-gated endpoint mid-request.
- `routes/auth.py:213` — beta-key expiry check used
  `dateutil.parser.parse()` which can return naive datetimes; a
  swallowed `TypeError` meant expired beta keys could pass.

**Verified safe (no fix needed)**: `services/firewall.py:127`
(uses internal `_parse_iso`), `services/providerrouter.py:279/326/334/338`
(`started` is locally created), `routes/admin_guard_shadow.py:300`
(explicit guard at lines 297-299), `services/ops_snapshot.py:154`,
`services/fred_service.py:90/237`, `ai_core/kill_switch.py:206/236`,
and others — all already handled their tzinfo correctly.

**Test status**: 15/15 new tests pass; full
`tests/test_forgot_password.py + test_authority_risk_budget.py +
test_risedual_ip_logic.py` regression suite (54 tests) green. Login
flow live-verified end-to-end.

### One-Click Patent Promotion + Manual-Order Outcome Grading + Calibration Polish (Apr 30, 2026)

**Per-patent enforcement promotion as a UI product**:
- `services/guard_policy_store.py` — Mongo-backed override store
  (`guard_policy_state` collection, single doc + history-capped at 50
  entries). Single source of truth for runtime policy: env vars are
  the deploy-time seed, Mongo overrides are the runtime-mutated
  truth. Process-local 30s cache keeps the IP contract's per-decision
  read O(1).
- `EnforcementPolicy.from_env()` now merges env defaults with
  `get_cached_overrides()` so any worker/loop sees the current
  policy. The IP contract calls `refresh_cache_if_stale()` once per
  decision (≤30s lag end-to-end).
- 3 new admin endpoints under `/api/admin/guard-shadow/policy`:
  `GET /policy` (effective + overrides + env_defaults + history),
  `POST /policy/promote` ({flag, value, note}), `POST /policy/clear`
  ({flag}). All owner-only with flag whitelist validation.
- New "Per-Patent Enforcement" section in `GuardShadowPanel.jsx`:
  5 rows (K / Auditor / Authority / M / I), each with status pill
  (ENFORCING green / SHADOW amber), Promote/Demote button, Clear
  button (only when override exists), and an "override" label. A
  collapsible "Recent changes" expander shows the audit history
  (timestamp + flag + value + actor email + note).
- Live-verified: promote enforce_auditor=false → bot ran → cleared
  → reverted, all without restart.

**Manual-order outcome grading (step 10 for non-bot trades)**:
- `services/manual_order_guard.py` now returns `proof_chain_entity_id`
  in its response. New `record_manual_order_outcome()` helper
  appends an OUTCOME_VERIFIED proof block to the same chain on
  close. Failure never blocks a close (wrapped + logged).
- `services/smart_order_service.py` — persists
  `proof_chain_entity_id` on the smart_orders row at fill;
  `cancel_smart_order` reads it back and calls
  `record_manual_order_outcome` with computed exit P&L. Pending /
  partially-filled cancels skip the OUTCOME block (no realized P&L
  to log). Mirrors the crypto_closer flow exactly.
- `routes/smart_orders.py` — captures `proof_chain_entity_id` from
  the guard response and threads it through `create_smart_order`
  via a side-channel field (avoids schema-bumping the public
  pydantic contract).

**Conviction Calibration page polish**:
- Removed redundant 3-card stats grid (Verified / Tagged / Window).
  Window was a duplicate of the selector buttons immediately above
  it; Verified+Tagged are now a single subtitle line under the
  heading: "· 101 verified · 56 tagged". Saves a full row of
  vertical space.
- "By raw confidence (fallback)" grid is now a `<details>`
  collapsible — closed by default when conviction-tagged data
  exists, auto-open when it doesn't. Operators get the conviction
  view first; the coarser confidence fallback is a click away when
  comparing.

**Tests**: 9/9 backend tests in `test_iteration156_guard_policy.py`
+ 34/34 prior backend regressions green. Lint clean across all
modified files. Frontend agent verified 100% (iteration_156).

### Step 10 (Outcome Grading) + Risk Quality KPIs Widget + Toxic Alert Live-Validated (Apr 30, 2026)

**Outcome grading wired (step 10 of the IP lifecycle)**: when a
crypto trade closes, `crypto_closer.py` now appends an
`OUTCOME_VERIFIED` proof block to the entry-side IP chain — same
`entity_id`, so the chain runs `SIGNAL_CREATED → ADVERSARIAL →
AUDITOR → AUTHORITY → FAILURE_MODE → RISK_BUDGET →
EXECUTION_ATTEMPTED → OUTCOME_VERIFIED` (8 blocks). Threading: the
bot now persists `proof_chain_entity_id` on every trade row at
fill time; the closer reads it back to anchor the outcome event.
Live verified: forced a backdated SOL close, the chain hash-verifies
end-to-end with `pnl=0.0, outcome="flat"` in the OUTCOME_VERIFIED
payload. Failure of the proof append never blocks a close (wrapped
+ logged).

**Risk Quality KPIs widget**:
- New backend endpoint `GET /api/admin/conviction/quality-kpis?weeks=N`
  (in `routes/admin.py`) bucketing the cleaned-up predictions
  collection by ISO week. Returns:
  - `unique_failure_patterns`: count of distinct
    `(symbol, failure_code)` tuples per week (post-dedup — the
    real KPI now that the toxic-spikes spam bug is fixed).
  - `calibration_gap`: `avg_confidence − empirical_accuracy` per
    week, with mixed-scale confidence normalisation (0-1 vs 0-100).
  - `summary.trend`: `improving` / `stable` / `degrading` /
    `insufficient_data` — head-vs-tail third comparison, robust to
    single-week outliers (>0.05 absolute gap delta flips the badge).
- New frontend component `frontend/src/components/admin/QualityKPIsStrip.jsx`
  mounted in the Conviction admin tab above the existing buckets.
  Two cards side-by-side:
  - **Unique Failure Patterns** card: bar chart (red ≥5 patterns,
    amber 1-4, slate empty) + this-week's-patterns list.
  - **Calibration Gap** card: signed-value display (color-coded —
    emerald <0.10, amber <0.18, red ≥0.18) + trend pill with
    direction icon + SVG line chart with dashed zero reference,
    cyan polyline, dot per populated week. Degrading-trend warning
    banner appears below the chart when applicable.
- Live values: `summary.trend = "improving"`, current_gap =
  -0.064 (healthy), current unique failures = 0 this week.

**Toxic-spikes alert live-validated** (so we don't have to wait for
tomorrow's cron): direct call to `nightly_cleanup()` produced 22
distinct retagged patterns, all unique by `(symbol, date,
confidence)` — zero duplicates. Email render shows just 1 NVDA
row, no collapse callout needed (data is clean). The user's
recurring "76 failures × 3 visible patterns" spam is structurally
impossible now — `MAX_DEDUP_HITS=5000` prevents the upstream cause,
the email-render dedup is a defense-in-depth that didn't even need
to fire.

**Tests**: 9/9 backend tests in `test_iteration155_quality_kpis.py`
+ all regression endpoints green (proof-chain, guard-shadow,
calibration). Lint clean across all 5 modified files.

### Guard Shadow Admin UI + Manual-Order IP Contract Unification (Apr 30, 2026)

**Guard Shadow admin panel** — `frontend/src/components/admin/GuardShadowPanel.jsx`,
wired into AdminPanel as new "Guard Shadow" tab (Insights group, Eye
icon). Surfaces the Decision Pipeline Guard shadow-rollout data:
- **Summary card** — 3 stats: Shadow Mode (ENFORCING/ON badge),
  Decisions Logged (allow/block split), Would-Block Rate (color-coded:
  green <10%, amber 10-25%, red ≥25%) with prescriptive subtitles
  ("Low — safe to enforce" / "Aggressive — review before enforcing").
- **Window selector** — 24h / 3d / 7d / 30d buckets.
- **By-source breakdown** — per-source allow/block counts and percent
  (`crypto_bot`, `manual_order:smart_orders`, etc).
- **Top blocking reasons** — for the "Aggressive" surface; shows which
  gates are vetoing trades the most.
- **Decision feed** — paginated raw rows with source / only-blocked /
  entity-substring filters. Click a row to expand → shows
  `would_notional` vs `executed_notional` (the actual data operators
  need to evaluate enforcement-readiness), risk multiplier, last
  proof hash.
- 17/17 frontend test cases passed live (iteration_154). Verified
  expanded rows correctly surface `would_notional=$113.75` vs
  `executed_notional=$325.00` — the head-to-head data the rollout
  playbook calls for.

**Manual-order path unified** — `services/manual_order_guard.py` now
calls `run_risedual_ip_decision` instead of
`run_guarded_decision_pipeline_async`. Manual orders (smart orders,
options trades, broker routes) now write the same 6-block IP chain
as the crypto bot: `ADVERSARIAL_DECISION → AUDITOR_VERDICT →
AUTHORITY_VALIDATED → FAILURE_MODE_CLASSIFIED → RISK_BUDGET_APPLIED →
EXECUTION_ATTEMPTED`. Auditor calibration veto + explicit authority
validation are now logged on every manual order (previously only
the 4-step K→M→I→J pipeline ran). Updated
`tests/test_manual_order_guard.py` to expect 6 events; 47/47 backend
tests green. Lint clean.

### Toxic Spikes Alert Spam Bug — Root-Caused & Fixed (Apr 30, 2026)

The recurring "76 high-confidence failures, only 3 visible patterns
× 15 copies each" spam email — the operator's long-standing
complaint. Root cause was NOT the price-anchor bypass I initially
suspected; live MongoDB inspection revealed every duplicate had
identical `price_at_prediction=$202.06`. The actual culprit was
`MAX_DEDUP_HITS = 1` in `services/prediction_tracker.py`. Each
prediction record could only be extended once (~30min lifetime under
continuous polling). A `signal_dispatcher` firing every 10 minutes
for 4+ hours produced ~8 fresh records per (symbol, direction)
session — same prediction at the same price, logged 15 times,
each verified independently as a "miss" → spammy alert.

**Fixes (3 layers)**:

1. **Root cause** — `MAX_DEDUP_HITS` raised from 1 → 5000 (effectively
   uncapped for any plausible production run; the price-similarity
   check `<0.2% drift` still forces a fresh record on real price moves).
2. **Defense-in-depth** — added `prediction_date` (YYYY-MM-DD) to every
   new prediction document so alert filters / digests have a clean
   date field, and added a `price <= 0` guard at the top of
   `log_prediction` that drops the row with a sentinel id rather than
   inserting a poison-anchor record.
3. **Email rendering** — `_toxic_spikes_html` now collapses
   `(symbol, date, confidence)` duplicates before the 10-row cap and
   surfaces a callout: *"collapsed N duplicate rows — same prediction
   logged multiple times. Showing distinct patterns only."* Even if
   future bugs slip dupes through, operators see distinct patterns,
   not spam.

**Historical data cleanup**: ran a one-shot Mongo dedup pass that
collapsed 1764 duplicate records (kept the oldest of each
`(symbol, direction, feature, user_id, confidence, price, calendar_day)`
group). Predictions dropped from ~2035 → 271; toxic count
dropped from 124 → **13** (the actual unique high-conf misses).

**Verified**: simulated the exact spam input (45 rows of NVDA/QQQ/SPY
× 15) through the email renderer — output is now 3 distinct rows
with the "collapsed 42 duplicate rows" callout. Lint clean. Backend
healthy after restart.

### Canonical IP Contract Wired Into Crypto Bot + Per-Patent Enforcement Flags (Apr 30, 2026)

**Bot now executes via the canonical contract.** Replaced the
`run_guarded_decision_pipeline_async` call site in
`services/crypto_paper_trader.py` with `run_risedual_ip_decision`. The
crypto fleet is now the live execution path for the full 7-block IP
chain — `SIGNAL_CREATED → ADVERSARIAL_DECISION → AUDITOR_VERDICT →
AUTHORITY_VALIDATED → FAILURE_MODE_CLASSIFIED → RISK_BUDGET_APPLIED →
EXECUTION_ATTEMPTED`. Verified end-to-end on the preview: bot ran, all
hashes verified (`valid=True`), 7 ordered events per trade.

**Per-patent enforcement flags** for staged rollout
(`EnforcementPolicy.from_env()`):
- `PATENT_K_ENFORCE` — adversarial enforcement
- `PATENT_M_ENFORCE` — failure-mode block_trade
- `PATENT_I_ENFORCE` — risk budget rejection
- `AUDITOR_ENFORCE` — calibration veto
- `AUTHORITY_ENFORCE` — authority expiry/countersignature

When a flag is OFF, the corresponding gate STILL RUNS (proof chain
captures intent) but its rejection is downgraded — the trade flows to
the next gate using the operator's pre-guard sizing. Stage rollout
maps directly:
- **Stage 1**: All flags = 0 → only proof chain mandatory.
- **Stage 2**: `PATENT_M_ENFORCE=1` → liquidity gaps + data-quality block.
- **Stage 3**: `PATENT_K_ENFORCE=1` → adversarial conflict blocks.
- **Stage 4**: `PATENT_I_ENFORCE=1` → risk budget enforces.

Defaults: every flag is on. Operators flip them to `0` for shadow
gates. The contract response includes a `policy` dict so callers can
audit which gates were active per decision.

**Effective-action plumbing**: gates always *propose* an action +
notional; downstream uses gate output only when the gate is enforced.
When shadowed, the operator's signal action and `base_notional ×
base_multiplier` flow through unchanged so the rest of the contract
inspects what the operator actually wanted to do, not the gate's
safer fallback.

**Tests**: 21/21 green in `test_risedual_ip_logic.py` —
+6 new policy tests (shadow-passes-through-K, shadow-passes-through-auditor,
shadow-keeps-original-notional, partial-only-failure-mode-enforced,
from-env-defaults, from-env-reads-off-flag). Lint clean.

**PRD.md split**: trimmed from 2587 → 235 lines; historic implementation
log moved to `/app/memory/CHANGELOG.md`. PRD.md now keeps recent
entries + architecture + pointer to CHANGELOG.

### Canonical IP Decision Contract + Shadow-Mode Rollout (Apr 30, 2026)

The patent stack is now expressed as a single legal/technical contract
(`services/risedual_ip_logic.py`) plus a shadow-mode middle gear so
operators can compare "would-block" vs "actually executed" before
flipping enforcement on.

**`services/risedual_ip_logic.py` — `run_risedual_ip_decision(ctx)`**:
The 11-step lifecycle codified as one master function. Every gate
hash-logs to the proof chain — including REJECTIONS — so the legal
audit trail is complete whether the trade fires or doesn't:
1. Observe market (caller)
2. Generate candidate (`CandidateSignal`)
3. **Adversarial validation** (Patent K) → `ADVERSARIAL_DECISION` block
4. **Auditor calibration veto** (new) → `AUDITOR_VERDICT` block
5. **Authority validation** (Patent H/I expiry+countersignature) →
   `AUTHORITY_VALIDATED` block
6. **Failure mode classify** (Patent M) → `FAILURE_MODE_CLASSIFIED` block
7. **Adaptive risk budget** (Patent I) → `RISK_BUDGET_APPLIED` block
8. Execute (caller's `ExecutionClient`) → `EXECUTION_ATTEMPTED|FILLED`
9. Hash-log everything (Patent J — already inline)
10. Grade outcome (separate flow)
11. Feedback into retrain (separate flow)

Plus the **`can_execute(decision)`** rule predicate and 4
non-negotiable invariants (canonical action, multiplier ≤ authority,
no HOLD with notional, no loosening without countersignature) baked
in as `assert`s — programming errors fail loudly, not silently.

**`services/auditor_calibration.py`** — the missing step-4 module.
Pure function `review_calibration(rolling_accuracy, calibration_gap,
signal_confidence)` returns PASS / CAUTION / VETO. Wide gaps veto
unconditionally; medium gap + high confidence is a compound veto.
Catches the "model unanimously confident but historically wrong"
failure mode that adversarial enforcement (Bull-vs-Bear spread)
doesn't.

**Shadow-mode middle gear** (`GUARD_SHADOW_MODE` env flag):
- `services/guard_shadow_log.py` — single-writer module that writes
  guard verdicts to a new `guard_shadow_log` Mongo collection. Never
  raises; a logging failure must not block live trading.
- Wired into `manual_order_guard.py` and `crypto_paper_trader.py` —
  when `GUARD_SHADOW_MODE=1` the guard runs end-to-end (proof chain
  populates), but the verdict is RECORDED rather than ENFORCED.
  Routes proceed with the original notional. Default: `0`.
- New admin endpoints under `/api/admin/guard-shadow/`:
  - `GET /summary?hours=N` — counts + would-block rate + top blocking
    reasons + per-source breakdown (`crypto_bot` / `manual_order:smart_orders` etc).
  - `GET /decisions?limit=N&source=...&only_blocked=true&entity_substr=...`
    — paginated raw feed.
- Live verified end-to-end on the preview: flipped flag on, ran the
  crypto fleet (SOL SHORT @ $83.84, $318.33 notional), shadow row
  persisted with `would_allow: true`, source=`crypto_bot`. Flipped
  flag off, summary correctly reflects `shadow_mode_enabled: false`
  while keeping the historic row queryable.

**Tests**: 15/15 green in `test_risedual_ip_logic.py` covering happy
path (6 proof events), every rejection branch (invalid action,
adversarial reject, auditor compound veto, expired authority,
failure-mode block, execution failure), the `can_execute` predicate,
dry-run mode, and 5 auditor calibration unit tests covering the
compound-veto edge cases. Lint clean across all 5 new files.

**The IP rule, in plain English**: An AI may propose, but only a
governed, adversarially validated, failure-aware, authority-scoped,
proof-logged decision may execute.

### Tier 1 Visual Polish + Paper/Live Consolidation + Exception Sanitization (Apr 30, 2026)

**Per-item Paper/Live pickers consolidated** (user request — single global switch):
- `SmartOrderPanel.jsx` — replaced the Paper/Live/Preview `<Select>`
  (line 184) with a read-only mode badge (`data-testid="smart-order-mode-display"`)
  that mirrors the global navbar `TradingModePill`. The "simulate"
  preview path is still reachable via the existing dedicated Simulate
  button on the panel.
- `TradingBotPanel.jsx` `CreateBotForm` — replaced the per-bot Paper/
  Live dropdown with a read-only badge (`data-testid="create-bot-mode-display"`)
  inheriting from `useTradingMode()`. New bots created in whichever
  mode the operator's navbar pill says.
- Single source of truth confirmed: every mode change now flows
  through `POST /api/trading-mode/switch` with the 30s cooldown +
  audit pipeline. No bypass paths.

**CryptoPaperDashboard re-skin** (cosmetic harmonisation):
- Migrated palette from `bg-neutral-950/900/800` (gray) to
  `bg-slate-800/40 + slate-700/30` so the panel matches every other
  admin tab (Shadow, Adversarial, Proof Chain, etc).
- "Run Bot" promoted to the cyan brand CTA (`bg-[#3DE8D9]
  hover:bg-[#7AEEE0]`); "Close Due Trades" demoted to a quieter
  slate variant — proper visual hierarchy between primary +
  secondary actions.
- Stat cards now use uppercase tracking-wider labels + monospace
  values; recent-trades rows use cyan selection ring matching the
  brand.

**OrderFlowHeatmap duplicate-key fix**:
- `key={row-${row.price}}` could collide when grid binning produced
  the same rounded price. Now `key={row-${ri}-${row.price}}`.
- Console "Encountered two children with the same key" warning
  on the heatmap grid is gone. The remaining duplicate-key warnings
  in the console come from the external `emergent-main.js` platform
  script (not app code) — nothing actionable on our end.

**Global exception handler review** (P2):
- 5× `f"Broker error: {exc}"` 502 leaks in `routes/options_trading.py`
  replaced with sanitized "Broker [order placement|spread routing|
  unavailable]" — full exception still logged via `log_error()` for
  ops debugging.
- 1× `error: str(exc)` leak in `routes/public_api.py` `/ml-signal/batch`
  (200-response bypassed the global sanitizer) replaced with stable
  `"signal_unavailable"` token. Real exception logged via
  `logger.warning`.
- 72/72 related backend tests green.

**Frontend agent verification**: 15/15 cases passed (iteration_153)
— mode badges render correctly + match global mode, dropdowns gone,
all 4 admin surfaces still render, Run Bot + Close Due Trades both
functional, OrderFlowHeatmap fix confirmed.

### Patent J Proof Chain Explorer — admin UI wired + Mongo-aligned hash (Apr 30, 2026)
- Wired `frontend/src/components/admin/ProofChainExplorer.jsx` into
  `AdminPanel.jsx` Insights group as new "Proof Chain" tab
  (ShieldCheck icon, sits next to Patent Watch).
- Fixed cross-environment hash drift in `services/proof_chain.py`:
  Mongo BSON Date strips tzinfo and truncates microseconds to
  milliseconds on round-trip. Added `canonical_created_at(dt)`
  helper used by both `build_proof_block` (write) and
  `verify_chain` (read). Wiped 81 stale dev blocks; fresh chains
  verify clean.
- 15/15 backend tests green; frontend agent: 7/7 cases.

### Earlier Architecture — Patents I, J, K, M wired into Decision Pipeline Guard
- Patent I (Authority-Scoped Risk Budgeting), J (Tamper-evident
  Proof Chain), K (Structured Adversarial Enforcement), M (Failure
  Mode Intelligence) — all live and guarding crypto bot, broker
  routes, smart orders, options trading. 116 backend tests green.
- `PATENT_GUARD_ENABLED` env flag controls activation.


- Wired `frontend/src/components/admin/ProofChainExplorer.jsx` into
  `AdminPanel.jsx` Insights group as new "Proof Chain" tab
  (ShieldCheck icon, sits next to Patent Watch). Stats card +
  searchable entity list + chain detail pane with Verified/Tampered
  badge.
- Fixed cross-environment hash drift in `services/proof_chain.py`:
  Mongo BSON Date strips tzinfo and truncates microseconds to
  milliseconds on round-trip, so the verifier-side recompute
  produced different SHA-256s than the insert-side hash. Added
  `canonical_created_at(dt)` helper that normalises to UTC tz-naive
  + ms-precision before isoformatting. Both `build_proof_block`
  (write) and `verify_chain` (read) and `routes/admin_proof_chain.py`
  use it so insert-time and verify-time hash material is byte-identical.
- Wiped 81 stale dev blocks (hashed under the pre-fix tz-aware iso
  form). Fresh bot run repopulated cleanly: 6 blocks across 3
  entities (BTC/ETH/SOL), `valid=true` on chain detail.
- **Tests**: 15/15 green across `test_proof_chain.py` (7),
  `test_proof_chain_e2e.py` (3), `test_decision_pipeline_guard.py`
  (3), `test_adversarial_enforcer.py` + `test_failure_mode_classifier.py`.
  Lint clean.
- **Frontend agent verified**: 7/7 test cases passed
  (iteration_152.json) — tab renders, stats card, entity list
  filters, chain detail loads, Verified badge shows, block expansion
  reveals prev_hash/payload_hash/actor/created_at + payload dropdown.

### Earlier Architecture — Patents I, J, K, M wired into Decision Pipeline Guard
- Patent I (Authority-Scoped Risk Budgeting), J (Tamper-evident
  Proof Chain), K (Structured Adversarial Enforcement), M (Failure
  Mode Intelligence) — all live and guarding crypto bot, broker
  routes, smart orders, and options trading. 116 backend tests green.
- `PATENT_GUARD_ENABLED` env flag controls activation.

## 3. Architecture
```
/app
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── hubs/
│   │   │   │   ├── WarRoomHub.jsx        ← NEW (Phase 1) — merges 5 AI surfaces
│   │   │   │   ├── StockDetailHub.jsx    ← NEW (Phase 2) — merges Company+StockFit+13F
│   │   │   │   ├── ResearchHub.jsx       ← v2 slimmed from 10→5 tabs
│   │   │   │   ├── OptionsHub.jsx        ← v2 icon-only
│   │   │   │   ├── WorkspaceHub.jsx      ← v2 13-icon tabs + legend
│   │   │   │   └── IconTabBar.jsx        ← NEW (Phase 4) — reusable icon tab strip
│   │   │   └── chat/                     (decomposed chat components)
│   │   └── hooks/
│   │       └── useV2Nav.js               ← NEW — feature flag (v2 is default)
└── backend/
    ├── services/
    │   ├── email_service.py              ← Rewritten (light-theme templates)
    │   ├── digest_service.py             ← Rewritten (pulls real market data)
    │   ├── referral_rewards.py           ← Wired to send tiered reward emails
    │   ├── sec_13f_service.py
    │   └── cusip_mapper.py
    ├── routes/
    │   ├── auth.py                       ← Admin panel bug fixed (role=admin access)
    │   ├── digest.py                     ← Preview returns new content_summary
    │   └── stockfit_13f.py
    └── scripts/
        └── merge_owner_into_admin.py     ← NEW — Red Slate → Admin account merge
```


## 4. Older Implementation History

Entries older than ~30 days (or beyond the most recent 5 features) live in
[CHANGELOG.md](./CHANGELOG.md) to keep PRD.md scannable.
When PRD.md crosses 700 lines, roll the oldest "What's Been Implemented"
entries out to CHANGELOG.md the same way.

