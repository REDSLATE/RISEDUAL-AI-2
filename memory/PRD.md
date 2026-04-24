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

## 4. What's Been Implemented (cumulative)

### Auto-Revert Safety Rail — measured self-correction (Feb 24, 2026)
- NEW helper `evaluate_auto_revert_candidates(db)` in
  `services/model_adaptation.py`. Runs AFTER each retrain logs
  its row (so it can count that retrain toward the window). For
  every active adaptation it pulls the last 3 retrain records
  that applied it and flips the rule to inactive when ALL gates
  pass:
  - **Consistency** — ΔR < −0.01 across ALL 3 runs (below epsilon)
  - **Coverage** — max `rows_matched / samples` ≥ 5% (small
    samples can't earn statistical confidence)
  - **Not risk-compression** — Δwin_rate ≤ 0 on all 3 runs
    (ΔR-negative + win-rate-UP is legitimate downside control,
    never a reason to revert)
  - **Grace period** — requires ≥ 3 runs of history
  - **Audit completeness** — if any of the 3 runs lacks
    `delta_mean_r` (pre-attribution-layer row), defer until
    history catches up
- Cooldown on re-creation is already handled by
  `_has_recent_adaptation(COOLDOWN_DAYS=7)` — auto-reverted rows
  (created within the 7-day window) block detection from re-adding
  the same `metric/direction`, so a flip-flop loop is
  structurally impossible.
- Env-gated: `ML_ADAPTATION_AUTO_REVERT_ENABLED=true` (default
  off). Paired with `ML_ADAPTATION_ENABLED` so nothing fires in
  detection-only mode.
- **Audit trail**: writes to a new `adaptation_audit` collection
  with `{action:"auto_revert", reason, deltas_r, deltas_wr,
  coverages, metric, direction, at}`. Also tags the adaptation
  row itself with `auto_reverted:true` + `auto_reverted_reason`
  so operator-revert vs safety-rail-revert is distinguishable.
  Narrated into the activity feed as a new
  `adaptation_auto_reverted` event (registered in
  `EVENT_TYPES` with 🧯 glyph; ML filter chip picks it up).
- **Admin UI**: `GET /api/admin/adaptations` now surfaces the
  last 10 auto-reverts as `recent_auto_reverts[]`.
  `ModelAdaptationsPanel` renders them in a rose-tinted
  `AutoRevertStrip` above the active list — each row is
  click-to-expand revealing the ΔR/Δwin-rate/coverage history
  that triggered the revert. Silent when the list is empty.
- 7 pytest cases in `tests/test_auto_revert_safety_rail.py` cover
  every gate (flag off no-op, happy-path revert, grace period,
  epsilon noise, low coverage, risk compression, missing
  attribution). Full regression 43/43 green. Ruff clean, mypy
  baseline 0→0.
- **The full closed loop** is now: **detect** toxic pattern →
  **adapt** row weights → **measure** impact (global + per-ad) →
  **correct** if persistently negative. Four layers of
  intelligence, each with its own env flag and audit trail.



### Counterfactual Impact: ΔR + Δwin-rate — global + per-adaptation (Feb 24, 2026)
- **Helper** `estimate_adaptation_impact(df, w_base, w_adapt)` in
  `ml_retrain_service.py`: re-weights existing R-multiple outcomes
  to produce the counterfactual "if these adaptation weights had
  been live, how would the expected outcome have shifted?"
  Returns `baseline_mean_r`, `adapted_mean_r`, `delta_mean_r`,
  `baseline_win_rate`, `adapted_win_rate`, `delta_win_rate`,
  `rows_covered_frac`. Empty dict if the training frame lacks
  `r_multiple` (warm-start).
- **Hook**: wired into the retrain pipeline right after
  `apply_adaptations_to_weights()` and before `model.fit`. Logged
  to `ml_training_log.adaptation_impact` AND mirrored into the
  per-row `adaptations_applied[]` list with per-adaptation
  `delta_mean_r` + `delta_win_rate`. Per-adaptation attribution
  isolates each rule's contribution by counterfactually resetting
  that rule's rows back to their pre-adaptation weight while
  keeping other rules' adaptations live.
- **Supporting changes**: `apply_adaptations_to_weights()` now
  accepts `return_masks=True` and returns a 3-tuple with aligned
  per-adaptation boolean masks (used for attribution).
  `_load_training_dataframe()` returns an extra `outcomes_df`
  carrying `r_multiple` + `return_1d` + `outcome` so the impact
  calc doesn't need the full training frame. Typed with
  `@overload` so mypy sees the right return shape per call site.
- **Admin UI**: `GET /api/admin/adaptations` now carries a
  `last_impact` block (latest retrain's impact + per-ad deltas).
  `ModelAdaptationsPanel` renders a new `ImpactStrip` above the
  list with 4 KPI tiles (ΔR, Δwin, baseline R/win, rows covered),
  an italic proxy-caveat line, and an amber "too broad" warning
  when coverage >80% with near-zero deltas. Each adaptation row
  gets inline `ΔR +0.040 · Δwin +2.0%` chips when per-ad impact is
  available.
- **Tests**: 4 new pytest cases in `tests/test_adaptation_impact.py`
  (empty frame, sign-aware shift, zero when weights equal, mask
  alignment with summary). Full regression 36/36. Mypy 0→0, ruff
  clean. Verified end-to-end with a synthetic ml_training_log row:
  ΔR=0.06 (+4.8pp), per-ad volume.liquidity/LONG ΔR=0.04 flowed
  through to the panel payload.
- **Closes the loop** one more level deep: the "Why did this
  adaptation exist?" drilldown already shows the toxic-event
  evidence; the impact strip now shows "and here's what it did to
  our expected return." Full narrative from failure → evidence →
  adaptation → quantified effect.



### Code-Review Triage (Feb 24, 2026) — 10-item report
Received a new code-review report with 10 findings. Validated each before acting:
- **#1 Circular import** → FALSE (`import ai_core` succeeds)
- **#2 exec/eval RCE** → FALSE (scanner flagged variable name
  `_pt_exec` and a comment `# ── Safe Expression Evaluator
  (replaces eval()) ──` above an AST-based safe evaluator)
- **#3 Hardcoded secrets in tests** → FALSE (placeholders like
  `token="test-token"`, `api_key="test-key"`, `token="x"`)
- **#4 47 undefined variables** → FALSE (pyflakes reports 0 in
  `routes/`, `services/`, `server.py`)
- **#5 240 missing hook deps** → FALSE (eslint
  `react-hooks/exhaustive-deps` reports 0 on flagged hooks)
- **#6 High complexity** → deferred (production-critical paths,
  per Feb-19 policy)
- **#7 localStorage "security"** → FALSE (UI preferences only;
  auth uses httpOnly cookies)
- **#8 Index-as-key** → **REAL**. Fixed in `AgentActivityFeed.jsx`
  (spike drivers + SHAP rows → composite keys), `MLHealthStrip.jsx`
  (RAdoptionCard sparkline bars → `r-${h.at}`),
  `UserWorkspace.jsx` (digest alert titles → composite),
  `ConvictionCalibration.jsx` (polyline/circle segments → composite).
- **#9 Empty catch blocks** → **REAL** in my own recent code
  (`TerminalModeHub.jsx` splitter pointer-capture). Replaced with
  `logger.debug()` calls that surface browser-compat fallbacks.
- **#10 `is` vs `==`** → mostly valid pytest patterns
  (`is True/False/None`); non-issue.

Net: 2 real findings, 8 false positives. Report appears generated
by a static-analysis tool that lacks comment/AST awareness — it
flagged "exec" in `_pt_exec`, the word "eval" inside a comment
explaining a safe evaluator, and placeholder test tokens. Saved
in this log so future reviewers can point at the same diff when
the same tool flags the same lines again.



### 4-item batch: magnitude retirement eval, admin audit, Terminal polish, digest shine (Feb 24, 2026)
- **Magnitude-path retirement plan (P2)**: `GET /api/admin/tier3-progress`
  now returns an `r_adoption` block with a 14-run `history[]` (each
  with `at`, `r_eligible_frac`, `r_skipped_frac`, `samples`) and a
  `verdict` object (`threshold`, `required_consecutive`,
  `consecutive_stable_runs`, `latest_r_eligible_frac`,
  `magnitude_retirement_ready`, `runs_tracked`). Filter now matches
  both `status=ok` and `status=success` (legacy rows). Admin UI
  gets a new `RAdoptionCard` in `MLHealthStrip` (6th column) with
  percentage, mini sparkline (teal when ≥70%, slate otherwise),
  and a green "Ready to retire magnitude path" cue once
  ≥3 consecutive stable runs. Full 3-step rollout memo at
  `/app/memory/MAGNITUDE_RETIREMENT_PLAN.md`.
- **Admin duplicate-button audit**: verified. Prior Feb-18
  refactor already scoped header Refresh to the Users tab, per-tab
  Refresh buttons each do their own thing. No new duplicates to
  consolidate.
- **Terminal Mode polish**: Splitter rewritten from `mousedown`
  stack to pointer events — trackpad drag, touch, and
  `setPointerCapture` so dragging off the splitter no longer
  drops the drag. Handle widened from 1px → 1.5px with hidden
  grip dots that fade in on hover (doesn't steal resting-state
  real estate). Double-click resets to default (52/55). Status bar
  swaps from static "Layout auto-saved" to "Layout modified · Reset"
  when the split is non-default. `touch-none` blocks mobile
  scroll-during-drag.
- **On-demand digest — "make it shine"**: new
  `GET /api/digest/my-preview` (user-level, not admin-gated) that
  returns a trimmed `{content_summary, preview:{overview_headline,
  top_predictions[3], top_smart_money[3], alert_titles[3]},
  email, generated_at}` payload. Frontend `DigestPreviewModal`
  renders KPI tiles + overview quote + top predictions/smart-money/
  alerts + watchlist-intel cue, with "Send to my inbox" confirm +
  "Cancel". Esc key closes (when not sending). Modal degrades
  gracefully on preview-fetch failure. `DigestToggle` exported as
  a named component and lazy-rendered at the top of
  `WorkspaceHub`'s Agent tab — finally reachable from the SPA
  (the legacy `UserWorkspace` modal was orphaned).
- **Testing**: iteration_140 (14 new backend tests + 19 regression
  all green, 100% backend, 95% frontend — only issue was the
  orphaned modal, now fixed). iteration_141 verified the fix; only
  low-priority Esc-key handler missing → added. Lint clean, mypy
  baseline 0→0, pytest 19/19 regression still green.



### Closed-Loop Explainability — Adaptation "Why?" + Activity enrichment (Feb 24, 2026)
- **New endpoint** `GET /api/admin/adaptations/why/{adaptation_id}`
  (admin-gated) resolves an adaptation into a full explanation
  payload: `metric`, `direction`, `factor`, `weight_reduction_pct`,
  `lift` (contrast), `bucket_rate`, `global_rate`, `severity`,
  `evidence_count`, `active`, `expires_at`, `description`,
  `explanation` (plain-English narrator keyed by metric prefix +
  direction → "Low-liquidity setups failed 1.52× more often than
  baseline on the bullish side."), and `projected_effect`
  ("Reduces influence of matching setups by ~15% in the next
  retrain").
- **Enriched activity payload**: `retrain_adaptation_applied`
  events now carry `metadata.mean_weight_before`,
  `mean_weight_after`, `mean_weight_delta` and each row inside
  `metadata.adaptations[]` picks up `lift`, `severity`,
  `evidence_count`, `description`, `direction`. Computed inside
  `apply_adaptations_to_weights()` right around `model.fit` —
  fire-and-forget, never blocks the retrain.
- **Frontend** `AdaptationBlock` component in
  `AgentActivityFeed.jsx` renders under every
  `retrain_adaptation_applied` event. Shows the weight transition
  line ("Mean weight 1.000 → 0.925 (Δ -0.075)"), amber chips per
  adaptation with metric/direction/factor/rows/%-down, and a
  teal "Why?" button that lazy-fetches the explain endpoint the
  first time it's opened, caches the response, toggles
  open/closed on repeat click, and inlines the explanation in a
  left-border callout. Projected-impact coverage line renders at
  the bottom when available.
- **Closes the narrative loop**:
  `alert_reserved → /alerts/why/{id}` (SHAP drivers per miss) →
  `retrain_adaptation_applied` (weight shift) →
  `/adaptations/why/{id}` (why the adaptation exists + what it
  does) → `retrain_complete`. Admins can trace any weight change
  back to the exact toxic pattern that justified it, without
  leaving the activity feed.
- 4 new pytest regression cases in
  `tests/test_adaptation_why_endpoint.py` (404, 401, full
  payload shape, end-to-end event enrichment). Full regression
  19/19 green. Testing agent iteration_139: 14/14 backend green,
  zero UI bugs, AdaptationBlock verified live.



### SHAP + Directional ML Adaptation — unblock + wire-up (Feb 24, 2026)
- **P0** Fixed 4× `E702` semicolon syntax errors (model_adaptation.py
  lines 733-740) + 2× `F541` f-string cleanups (lines 441-442)
  left from prior session. Added explicit `dict[str, Any]` annotation
  at line 688 so `cond` can accept bool/str values under strict mypy
  (resolves 3 `[dict-item]` gate failures). `/app/scripts/typecheck.sh`
  baseline back at 0.
- **P1** Fixed real UI bug in `AgentActivityFeed.jsx` — the
  `SpikeDetailsBlock` was rendered without its `alertId` prop so the
  `/api/admin/alerts/why/{id}` enrichment fetch never fired and the
  SHAP bullets stayed empty. Now passes `alertId={event.metadata?.alert_id}`.
- **Verified end-to-end**: synthesised a toxic-spike alert → GET
  `/api/admin/alerts/why/{id}` returns `shap_top` with signed XGBoost
  `pred_contribs` values per ticker (AAPL: macd=+0.0721, NVDA:
  rsi_14=-0.1493), averaged across CalibratedClassifierCV folds.
  Admin panel `ModelAdaptationsPanel` shows direction + contrast +
  severity + evidence badges.
- Pytest 15/15 green (test_iteration59_toxic_spikes_alert +
  test_signal_model_sample_weight). Testing agent iteration_138:
  zero critical backend/frontend issues.
- Side cleanup: dropped the unused in-function `from datetime import …`
  re-import in `signal_model.py::predict()`; added explanatory
  `# noqa: F401` on the numpy guard that powers the `"np.ndarray"`
  forward-ref annotation.



### mypy baseline 47 → 0 (clean slate) (Feb 22, 2026)
- Eliminated all remaining type errors in `backend/services/`.
  Categorised into 5 patterns: mixed-value dicts needing
  `dict[str, Any]` annotations (12 files), BeautifulSoup
  `.get('href')` union-attr guards (scraper hardening), ChromaDB
  stub strictness (targeted `# type: ignore[arg-type]`), SDK
  TypedDict strictness (scoped ignores), and one **real runtime
  bug** — `referral_rewards.py` imported a non-existent
  `send_to_user` from `push_service.py` (silent-fail try/except
  was masking it). Added a proper user-scoped push helper —
  referral reward notifications now work.
- Fixed `typecheck.sh` pipefail bug where `grep` returning empty
  caused the whole gate to exit non-zero.
- Baseline locked at 0. Any new mypy error from now on is a real
  signal. 239/239 tests green.

### Smart-routed spreads + mypy 69→47 + Tier 3 paper-days tile (Feb 22, 2026)
- **P2**: `SmartOrderRouter.route_spread()` — new
  `supports_multileg` capability flag on the adapter interface
  filters out quote-only brokers before scoring. `POST
  /api/options/spread` now accepts `best_execution=true`.
  Live-verified on Alpaca paper: 2-leg AAPL 190/195 call debit
  vertical, order accepted + cancelled.
- **P3**: mypy baseline reduced 69→47 (32%). Seven services had
  `set_db(database: object)` + `db = None` making the module-level
  `db` resolve to `object`; swapped to `Any`. Added targeted
  `dict[str, Any]` / `list[str]` annotations where mypy had
  real signal. Two BS scraper `.get('href')` sites hardened
  against malformed HTML. No runtime behaviour change.
- **P1**: New `PaperDaysProgressCard` in `MLHealthStrip.jsx` —
  4th card in the admin Conviction strip, backed by
  `/api/admin/tier3-progress`. Shows `days/30`, progress bar,
  remaining-days + env override. Grid widens to
  `sm:grid-cols-2 lg:grid-cols-4`.
- Tests: 5 new router tests + testing-agent 14/14 backend pass.
  Testing agent code-reviewed the frontend tile. 239/239
  regression tests green.

### Options Phase 2: Greeks, Tradier quotes, multi-leg spreads, ODD audit (Feb 22, 2026)
- `compute_greeks()` + `compute_greeks_for_contract()` in
  `ai_core/options_pricing.py` — delta/gamma/theta/vega/rho using
  retail conventions (theta per calendar day, vega/rho per 1%).
  Matches Hull reference within 1% at ATM.
- `services/brokers/tradier_options.py` — concrete quote-only
  adapter. `fetch_tradier_option_quote` handles Tradier's nested
  `{quotes: {quote: {...}|[...]}}` envelope + the `"null"`
  literal-string quirk. Never raises.
- `SmartOrderRouter._estimate_spread` now resolves in 3 tiers:
  adapter's own `try_get_spread()` → Tradier proxy-spread (NBBO
  is routing-agnostic) → sentinel. Keeps single-broker case
  working without regression.
- Alpaca multi-leg `mleg` envelope — up to 4 legs, ratio math
  with common-multiplier enforcement. `position_intent` dropped
  the same way single-leg required.
- `GET /api/options/greeks` (auth-gated preview) + `POST
  /api/options/spread` (ODD-gated, 2-4 legs, opening-leg
  required). Non-Alpaca providers still return 501 on spreads.
- `_log_order_audit()` writes every live single-leg AND spread
  order to a new `option_orders` collection with `odd_accepted_at`
  stamped on the record itself — closes P2 regulatory audit.
- Tests: 26 new (10 Greeks + 12 Tradier + 4 multi-leg + stub
  registry fix). Testing-agent validated **20/20** Phase 2
  features against live API. mypy gate 69. Ruff clean.

### Structured-Log Migration (156 sites) + 2 CancelledError Fixes (Feb 20, 2026)
- Migrated **156 logger.warning/error sites across 21 services** to
  `log_warning(logger, {...})` / `log_error(...)`. Every operational
  event in prod is now queryable by `context` and `type` fields.
- Built `/app/scripts/migrate_logs.py` — parses f-string bodies,
  extracts exception vars + interpolations, inserts imports. Left
  117 rarer-pattern sites for manual review (safer than mechanical).
- Fleet-wide audit for the `isinstance(x, Exception)` anti-pattern
  after `asyncio.gather(return_exceptions=True)` — found **2 more
  real CancelledError crash paths** (war_room_service + crew_engine)
  on top of the FRED one from earlier. All three now use the
  three-tier guard with structured logging.
- mypy baseline: 85 → **77** (8 more errors resolved as a knock-on
  from properly narrowing exception handling).

### [union-attr] Crash-Path Sweep (COMPLETED Feb 20, 2026)
- Audited and fixed all 13 mypy `[union-attr]` errors. **Found one
  real reachable runtime bug**: FRED service's
  `asyncio.gather(return_exceptions=True)` result-handling used
  `isinstance(result, Exception)` as the skip-guard, but
  `asyncio.CancelledError` is a `BaseException` subclass (not
  `Exception`) since Python 3.8. Cancelled tasks slipped past the
  guard and crashed at `result.get("observations", [])` with
  `AttributeError`. Widened to `BaseException`.
- 2 defensive hardening fixes: BeautifulSoup `href` attribute
  isinstance guards (scraping resilience), Anthropic content-block
  `isinstance(TextBlock)` narrowing (annotation clarity).
- 3 regression tests (`tests/test_fred_baseexception_guard.py`)
  reproduce the `CancelledError` crash and verify the fix. Confirmed
  to FAIL on the old code before the widening.
- mypy baseline 98 → **85** (`[union-attr]` category: 13 → 0).

### P1 RESOLVED: Alpaca Cover-Order Verification (Feb 20, 2026)
- Verified live against the paper account: **0 open shorts**,
  **0 open positions**, **0 orphaned pending orders**, equity
  $102,027.38 (net positive). Covers appear to have filled cleanly
  at market open.
- Built `GET /api/admin/alpaca-health` — reusable admin endpoint
  that reports account/position/order state + a boolean verdict
  (`covers_clean`, `no_orphan_orders`, `trading_enabled`) so any
  future cover workflow can be re-verified with one click.
- 7 auth-matrix + shape-invariant tests; the live broker path is
  smoke-tested per session (documented in DEPLOYMENT_NOTES.md).

### mypy Baseline 136 → 98 (COMPLETED Feb 20, 2026)
- Reduced the mypy baseline by 28% (38 errors) via three safe
  sweeps: `types-requests` stubs install, 16 var-annotated fixes,
  5 `[valid-type]` SDK-annotation-to-`Any` swaps, 4 real annotation
  bugs (2 `chat()` / `fetch_earnings_surprises` return-type lies,
  2 numpy `floating[Any]` → `float` casts).
- 98 errors remain in the baseline. Top categories still require
  case-by-case judgment: `[attr-defined]` 21, `[arg-type]` 16,
  `[union-attr]` 13 (**each a potential `None` dereference**),
  `[assignment]` 12, `[operator]` 10.
- Gate locked at 98. Any new error fails the pre-deploy check.

### Admin Allocation-Preview Endpoint (COMPLETED Feb 20, 2026)
- `GET /api/admin/allocation-preview?total_capital=<USD>` — wraps
  the new Drawdown Allocator in an admin-only preview. Returns the
  allocation split, per-bot scores, and the source stats for each
  enabled bot in the `trading_bots` Mongo collection.
- Auth: `_require_admin` — 401 anon, 403 non-admin, 200 owner/admin.
- `total_capital ≤ 0` rejected with 400. Empty fleet returns
  `{allocations: {}, bot_count: 0, note: "no enabled bots"}`.
- Derives `win_rate` from `stats.winning_trades / stats.trades` when
  the dedicated field is absent; `None` is passed to `compute_bot_score`
  (which defaults to 0.5) so brand-new bots get a fair share.
- **12 new integration tests** (`tests/test_admin_allocation_preview.py`)
  covering the auth matrix, query-param validation, response shape,
  allocation math (sum ≈ total_capital), and score-floor invariants.

### Drawdown Control + Multi-Bot Capital Allocator (COMPLETED Feb 20, 2026)
- New pure-function module `ai_core/drawdown_allocator.py` with
  five primitives: `compute_drawdown`, `compute_drawdown_multiplier`,
  `compute_bot_score`, `allocate_capital`, and
  `apply_global_risk_controls`.
- Thresholds: `SOFT_DRAWDOWN=10%`, `MAX_DRAWDOWN=20%`,
  `MIN_RISK_MULTIPLIER=0.3` (floor — never cut risk below 30% so
  Tier 3 accuracy stats keep accumulating). Linear taper between
  soft and max.
- Bot score: `0.7 × win_rate + (0.3 if pnl > 0 else 0)`, floored
  at 0.1 so losing bots rehabilitate instead of getting starved.
- `execute_signal` gained two opt-in kwargs (`equity_curve`,
  `bot_capital`). When both supplied, a new step 3c applies the
  global risk controls between the portfolio gate and the hard
  cap; skips with `reason="risk control"` when the combined
  multiplier zeroes the trade.
- Caught a real bug during test-writing: original
  `compute_bot_score` used `X or 0.5` as the fallback, which
  clobbered legitimate `0.0` values (Python treats 0.0 as falsy).
  Switched to explicit `None` check.
- **39 new tests** in `tests/test_drawdown_allocator.py`; full
  regression green at **248/248** across the trading-engine test
  surface; lint + mypy baseline-diff both pass.

### mypy Pre-Deploy Gate (COMPLETED Feb 20, 2026)
- Wired a baseline-diff mypy gate at `/app/scripts/typecheck.sh`
  with config at `/app/backend/mypy.ini` and a snapshot of the
  current 136 error signatures at `/app/scripts/typecheck_baseline.txt`.
- Three modes: default (run & diff, exit 1 on new errors),
  `--update` (lock in fixes as new baseline), `--list` (print).
- Normalises mypy output (drops line/col numbers, sorts) so unrelated
  refactors don't flap the gate. Focuses on file + error-code + message.
- Lenient config (`ignore_missing_imports`, `no_strict_optional`,
  `follow_imports = silent`) — catches the categories that break
  prod (`[return-value]`, `[attr-defined]`, `[syntax]`, `[arg-type]`)
  without forcing cleanup of ~2k third-party-sdk noise.
- **Verified** catches: return-type mismatches (exit 1), syntax
  errors like the orphan-lines issue that lazy-loaded silently
  past ruff (exit 1). Clean state exits 0.

### Type-hint Coverage 100% in `/backend/services/` (COMPLETED Feb 20, 2026)
- Pushed Python type-hint coverage across **866 functions** in the
  services directory from **88.4% → 100%**. Every parameter + return
  type now explicit. Closes the long-standing P3 code-quality item.
- Standard patterns applied: `set_db(database: Any) -> None`,
  `-> EngineResult` on all 10 `/search_war_room/adapters/*.run(...)`
  entries, `np.ndarray` / `tuple[float, float, float]` on technical
  indicator helpers, `Any` on schema-agnostic crew/agent signatures.
- Added `from typing import Any` to ~15 files that previously didn't
  need it.
- Side catch: `services/ai_intelligence_service.py` had 3 orphan
  tail lines (dangling `datetime.now(timezone.utc).isoformat()`
  fragment from a pre-existing bad merge) — removed during the
  sweep. Python's lazy import had masked it.
- Ruff lint clean on the entire directory. **209/209** regression
  tests green.
- Also dropped "Adversitao Everywhere" from the P3 roadmap per user
  request (see `/app/memory/ROADMAP.md`).

### Portfolio Risk Engine + NewsAPI.ai Metadata Flags (COMPLETED Feb 20, 2026)
- **Portfolio Risk Engine** (`services/trading_bot_service.py`): three
  global caps on top of the existing per-trade `MAX_POSITION_USD=$2000`
  limit.
  - `MAX_PORTFOLIO_EXPOSURE=$3000` (aggregate notional across open
    positions).
  - `MAX_CONCURRENT_TRADES=5` (concurrency).
  - `MAX_SECTOR_EXPOSURE_PCT=0.50` (no single sector > 50% of total
    exposure — prevents stacking a 3rd tech long at the top).
  Helpers: `get_total_exposure`, `get_open_trade_count`,
  `get_sector_exposure`, and `apply_portfolio_constraints`
  (concurrency-first → total-exposure headroom → sector gate →
  shrink-to-remaining). `execute_signal(..., open_positions=...)`
  takes the opt-in kwarg and passes `signal.get("sector")` through;
  trades that saturate any cap skip with
  `reason="portfolio limits reached"` (no broker order fires).
  Untagged signals / omitted kwarg preserve backwards-compatible
  behaviour.
- **NewsAPI.ai metadata flags**
  (`services/search_war_room/adapters/newsapi.py`): opt-in fields
  from the NewsAPI onboarding email — `includeArticleImage`,
  `includeArticleConcepts`, `includeArticleCategories`,
  `includeSourceRanking`, `includeSourceImage`. Response parser now
  surfaces `image`, `source_image`, `source_ranking` (Alexa rank),
  top-3 `concepts` (label/type/score), and top-2 `categories` on
  each item — used by the War Room UI for richer cards.
- **Tests**: 39 new tests — `test_portfolio_risk_engine.py` (35,
  incl. sector gate) and `test_newsapi_metadata_flags.py` (4).
  Full regression green: **92/92 passing** across portfolio,
  USD-notional, adaptive-sizing, Tier 3, and NewsAPI surfaces.

### risedual_core Refactor Overlay (COMPLETED Feb 19, 2026)
- User uploaded a pre-tested refactored zip of `risedual_core`. I did NOT
  apply blindly — verified:
  - Current hypothesis_logger.py had a **silent bug**: importing
    `risedual_core.ml.patterns` (doesn't exist locally), caught by a broad
    `except` — so chart-pattern enrichment was silently failing in prod.
    The upload ships `ml/patterns.py` (708 lines) and FIXES this.
  - The upload supersedes my prior `_parse_response` extractions in
    anthropic.py/openai.py with a cleaner module-level pure function
    version. Both approaches achieve the same goal; the module-level one
    is better (pure function, no self dependency).
  - Added `_handle_retry` helper in `clients/base.py`.
  - `pyproject.toml` adds optional `keyvault = ["cryptography>=42.0.0"]`
    extras — non-breaking.
  - Upload deletes `ml/calibration_gate.py`; preserved our existing compat
    shim because `scripts/backtest.py` still imports from it.
- **Safety process**: backup to `/tmp/risedual_core.backup.*`, overlay
  files, clear `__pycache__`, restart backend.
- **Verified post-overlay**:
  - All 8 changed modules import cleanly.
  - Backend restarts with no errors.
  - `/api/crypto/prices`, `/api/fear-greed`, `/api/stocks/quote/AAPL` all 200.
  - `detect_all_patterns()` returns 8 pattern classifications on a sample
    5-row OHLCV (previously silently failed).
  - 0 undefined names across `risedual_core/`, lint 100% clean.

### Conviction Calibration Admin UI (COMPLETED Feb 20, 2026)
- New admin Insights tab **Conviction** reading `GET /api/admin/conviction/calibration?days={7|30|90}`.
- Backend endpoint buckets verified predictions (`verified_24h.correct` set) by either `conviction.score` (primary) or `confidence` (fallback), returning per-bucket totals, correct counts, win-rate, and a monotonic-health boolean.
- `prediction_tracker.log_prediction()` now accepts an optional `conviction` dict; field only persisted when supplied.
- Live at ship: 182 verified / 30d, confidence curve monotonic=true (Low — · Medium 52.7% 87/165 · High 88.2% 15/17). Conviction buckets empty pending call-site wiring.
- Files: `backend/services/prediction_tracker.py`, `backend/routes/admin.py`, `frontend/src/components/admin/ConvictionCalibration.jsx`, `frontend/src/components/AdminPanel.jsx`.
- Follow-up (P1 backlog): pass conviction dict from `routes/ai.py` & `routes/intelligence.py` when logging predictions so the `by_conviction` badge activates.

### Chat Component Split (COMPLETED Feb 19, 2026)
- Split `chat/ChatComponents.jsx` (440L monolith) into three focused files
  while preserving backward-compat imports via a 7-line shim:
  - `chat/ChatMessages.jsx` (238L) — message list + bubble + chip/action
    adoption telemetry.
  - `chat/ChatInput.jsx` (188L) — input bar, voice recording, gap-hint
    banner. Exports as both `ChatInput` and `ChatInputArea` (legacy alias).
  - `chat/VoiceSelector.jsx` (30L) — voice toggle.
  - `chat/ChatComponents.jsx` (7L) — thin re-export shim.
- E2E verified: chat opens, messages list renders, input accepts "hello".

### LLM Provider chat() Refactor (COMPLETED Feb 19, 2026)
- Pushed back on the original suggested `_handle_streaming` / `_handle_standard`
  split — there is no streaming code in either provider, so that pattern
  didn't apply. Took the safe extraction instead.
- **Extracted** the response-parsing block in both
  `risedual_core/risedual_core/llm/anthropic.py` and
  `risedual_core/risedual_core/llm/openai.py` into a private
  `_parse_response(response) -> LLMResponse` helper.
- **Line counts**:
  - `anthropic.chat()`: 105 → 62 lines. New `_parse_response()`: 51 lines.
  - `openai.chat()`: 99 → 56 lines. New `_parse_response()`: 48 lines.
- Both classes still import and expose the same public API. `chat()` is
  now a clean linear flow: build kwargs → API call → `_parse_response`.
- Verified: lint clean, imports work (`AnthropicLLM.chat` /
  `AnthropicLLM._parse_response` both callable), backend restarted
  healthy, `/api/crypto/prices` 200.

### React Hooks Exhaustive-Deps Sweep (COMPLETED Feb 19, 2026)
- User asked to run `npx eslint src/hooks/ --rule '{"react-hooks/exhaustive-deps": "error"}'`.
- **Result: 0 errors** across every custom hook flagged in the original
  code review (`useTTS`, `useStreamingAgent`, `usePushNotifications`,
  `useChatMemory`, `useReferralCapture`, `useModals`). The original
  reviewer's "missing dependencies" claim was categorically wrong.
- Ran the rule across all of `src/` — still 0 errors. Auto-fix removed
  8 orphaned `// eslint-disable-next-line react-hooks/exhaustive-deps`
  comments in `AuthContext.jsx`, `MobileBottomNav.jsx`, and
  `ShareSmartMoneyBoard.jsx` that were suppressing warnings no longer
  fired. Pure cosmetic cleanup.
- Final state: **0 errors, 0 warnings** under strict exhaustive-deps.

### Pyflakes Deep Scan + Re-export Bug Fix (COMPLETED Feb 19, 2026)
- User asked to run `pyflakes` across the whole backend. Found 12 actual
  undefined names (all in non-runtime code):
  - Fixed `scripts/backfill_insider_edgar.py`: missing `SEC_BASE` constant
    added (`"https://data.sec.gov"`).
  - Fixed `tests/test_iteration96_auto_invite.py`: missing `import sys`.
  - Fixed `scripts/train_signal_model.py`: 10 `pd` forward-ref complaints
    resolved by adding `TYPE_CHECKING` guarded `import pandas as pd`.
- **CAUGHT + FIXED a real production bug** introduced earlier: the
  `ruff --fix` pass had aggressively removed `seed_admin` and
  `create_indexes` from `route_registry.py`'s `from routes.auth import …`
  line, thinking they were unused locally. But server.py re-imports them
  from `route_registry`. Backend was crashing on startup with
  `ImportError: cannot import name 'seed_admin'`. Restored the re-exports
  with `# noqa: F401` comment and a warning comment.
- **Final status**: 0 undefined names across the whole backend. All
  services healthy, all tested API endpoints responding 200.

### Code Review Pass (COMPLETED Feb 19, 2026)
- **Fixed**: replaced array-index React `key`s with stable data-driven keys in
  the components I own — `ChipAdoptionInsights.jsx` (3 tables),
  `HelpSearchInsights.jsx` (2 tables), `ChatComponents.jsx` (chips +
  actions using `${idx}-${text}`), `HelpCenter.jsx` (suggestions + results).
  Prevents React reconciliation bugs when lists re-order.
- **Reviewed & declined (false positives)**:
  - "eval() in backtester_service.py line 193" — it's a COMMENT saying
    "Safe Expression Evaluator (replaces eval())". The code is an
    AST-based safe evaluator (`_CMP_OPS`, `_BIN_OPS` using `operator`
    module), not eval. No security issue.
  - "Hardcoded secrets in tests" — these are TEST credentials from
    `test_credentials.md` (e.g. admin@risedual.ai) used by pytest
    fixtures. Not production secrets.
  - "exec/eval in test_iteration36_code_quality.py" — those are SECURITY
    TESTS named `test_rejects_exec` / `test_rejects_eval` that verify the
    app REJECTS dynamic code execution. Part of the safeguard, not a risk.
  - "17 undefined variables" — zero in runtime code
    (routes/services/server.py). All F821 errors are in standalone
    `backend/scripts/` using string-forward-refs like `"pd.DataFrame"`.
- **Reviewed & deferred (post-deploy)**: component size refactors
  (Watchlist 428L, Navbar 346L, RiseDualGPTChat 350L), LLM `chat()`
  function decomposition, `useV2Nav/useTTS/useStreamingAgent` missing
  hook deps. These are real improvements but touch production-critical
  paths on the eve of deploy — post-deploy work with proper QA.

### Misclick Rate + Pre-Deploy Cleanup (COMPLETED Feb 19, 2026)
- **Backend stats**: `/api/analytics/chip-events/stats` now classifies
  `action-clicked` events into forward-clicks and undo-clicks (chip_text
  starts with `"Undo "`) and returns:
  - `undo_count` + global `misclick_rate` = undos / forward-clicks
  - Per-hub `undo_clicked` + `misclick_rate`
  - `top_actions` now EXCLUDES undo entries (leaderboard shows what users
    actually want, not what they bounce from)
- **Admin UI**: new `Misclick` column in Per-Hub Breakdown table — red ≥25%,
  amber ≥10%, slate otherwise. Hover title shows raw counts.
- **Bug found + fixed (pre-existing)**: `backend/routes/analytics.py` had a
  broken duplicate `trigger_help_search_digest` endpoint at line 276 with no
  success return body. FastAPI was registering two routes for the same
  path — the stub could have taken precedence over the real one. Deleted.
- **Auto-fixed 499 unused imports** across `backend/routes/*.py` via ruff.
  All runtime code (routes/, services/, server.py) is now lint-clean except
  1 cosmetic unused-local in server.py. Remaining backend lint noise is
  entirely in standalone `backend/scripts/` which aren't imported at runtime.
- Frontend lints 100% clean (`components/`, `utils/`, `App.js`).
- **E2E verified**: stats endpoint returns `action_clicked=12, undo_count=1,
  misclick_rate=0.083`, Misclick column renders correctly in the admin panel.
- **Ready to deploy.** ✅

### Undo Last Deep-Link Toast (COMPLETED Feb 19, 2026)
- Every call to `openWarRoomForTicker` now shows a sonner toast bottom-right
  ("Analyzing XLK · From Sector Heatmap · [Undo]") with a 5s duration and
  an Undo button.
- Undo dispatches `risedualai-navigate` back to the view the user was on
  (snapshotted via `window.__risedualActiveView` before the nav), so any
  misclick on a tile is reversible with one click.
- Undo actions also log `action-clicked` telemetry with chip_text like
  `"Undo XLK War Room (Sector Heatmap)"`, giving the admin Chip CTR
  dashboard a proxy for misclick rate per source.
- Toast suppressed when origin was already `warroom` (no meaningful back
  state) to avoid a "noisy" experience once the user is inside the hub.
- **App.js fix along the way**: `window.__risedualActiveView` is now
  synced via `useEffect` on activeView changes (was only written during
  `navigateTo`, so it was `undefined` on initial mount — which broke the
  undo snapshot on the very first tile click).

### Component Sweep + Deep-Link Consolidation (COMPLETED Feb 19, 2026)
- **Deleted orphans** (no consumers anywhere):
  - `components/intelligence/ScoreView.jsx`
  - `components/BotsDashboard.jsx`
  - `components/CryptoSection.jsx` (already removed in previous commit)
- **New shared util** `/app/frontend/src/utils/deepLink.js` exporting a single
  `openWarRoomForTicker({ticker, source, subTab?, suffix?})` helper that:
  1. Logs `action-clicked` telemetry to `/api/analytics/chip-event`
  2. Dispatches `risedualai-navigate` to the right hub/subtab
  3. Dispatches `risedualai-warroom` ticker broadcast
- **Refactored 5 callers** to use the helper — previously each had a
  hand-written ~18-line try/fetch/dispatch block:
  - `components/heatmap/SectorTile.jsx` (2 call sites: AI view + price view)
  - `components/CryptoTicker.jsx` (crypto tile)
  - `components/FearGreedGauge.jsx` (verdict tab, with `suffix` carrying the
    live regime)
  - `components/Watchlist.jsx` (SM shift alert + per-row SM Board button)
- **Net reduction**: ~90 lines of duplicated code deleted. Changing telemetry
  shape or adding a step now means editing one file.
- Lint clean. E2E verified on 3 independent surfaces (XLF sector, ETH crypto,
  Fear & Greed) after the consolidation — zero runtime errors.

### Fear & Greed Verdict Tab Redesign + Clickable Deep-Link (COMPLETED Feb 19, 2026)
- Replaced the semi-circular gauge arc with a **verdict-tab card** that mirrors
  the AI War Room PASS/VETO / BULLISH / BEARISH visual language.
- **Clickable**: the whole verdict tab is now a `<button>`. One click fires:
  - `action-clicked` telemetry with a rich chip_text capturing the live
    regime at click time, e.g. `"Open SPY War Room (Fear & Greed: GREED 68)"`
    — so admin `Chip CTR` can track not just adoption but WHICH sentiment
    regimes users act on.
  - Navigates to War Room Adversarial subtab.
  - Dispatches `risedualai-warroom` with `SPY` (S&P proxy).
- Hover affordance: the date swaps to a subtle "Ask War Room →" hint (band-
  colored) so the deep-link intent is discoverable without cluttering the
  resting state.
- Verified E2E: clicked verdict at 68 GREED → War Room opened → SPY
  auto-analyzed ("Deploying War Room for SPY" running Strategist + Auditor).

### Markets Density Toggle (COMPLETED Feb 19, 2026)
- New `MarketsSection.jsx` component hosts both heatmaps with a 3-way
  segmented toggle: Both · Crypto · Sectors (icons: LayoutGrid · Bitcoin ·
  BarChart3). Default is "Both". Persisted in `localStorage` under
  `risedual:markets-view`.
- Moved `CryptoTicker` out of the global top-of-app strip (was rendered on
  every view under the navbar) into the Dashboard Markets section — reclaims
  vertical space on Research/Options/Workspace views where it wasn't needed.
- `CryptoTicker` component slimmed: removed its own `bg/border/padding`
  chrome so it can be embedded cleanly inside the new wrapper.
- Verified E2E: default `Both` shows both heatmaps; clicking `Crypto`
  collapses sectors; clicking `Sectors` collapses crypto. localStorage
  persistence confirmed across reloads.

### Crypto Heatmap Tiles with War Room Deep-Link (COMPLETED Feb 19, 2026)
- Rebuilt `CryptoTicker.jsx` from an auto-scrolling horizontal marquee into a
  responsive grid (`grid-cols-2 sm:grid-cols-4 lg:grid-cols-8`) matching the
  `SectorTile` visual language — color-coded by % change (heat scale tuned
  tighter for crypto volatility: ≥5% deep green, ≤-5% deep red).
- Each tile is now a clickable `<button>` → same deep-link bundle:
  telemetry `action-clicked` with source `"Crypto Heatmap"` → navigate to
  War Room Adversarial → dispatch ticker broadcast. BTC / ETH / BNB / SOL /
  XRP / ADA / DOGE / AVAX all route correctly.
- Verified E2E: clicked BTC tile → War Room opened → BTC auto-analyzed.
  Mobile + desktop layouts confirmed.
- Admin `Chip CTR` panel now differentiates three heat-source flavours in
  top-actions: `Sector Heatmap`, `AI Sector Heatmap`, `Crypto Heatmap`.

### Sector Heatmap Deep-Link + Recent Tickers Strip (COMPLETED Feb 19, 2026)
- **Sector Heatmap tiles** (`SectorTile.jsx`) are now clickable `<button>`s.
  One click on any sector ETF (XLK, XLF, XLV, etc.) — either the "AI sentiment"
  view or the classic "% change" view — dispatches the standard deep-link
  bundle: telemetry (`action-clicked`) → nav to War Room adversarial tab →
  ticker broadcast. Source tag in chip_text: `"Open XLK War Room (Sector
  Heatmap)"` / `"(AI Sector Heatmap)"` so the admin dashboard can split them.
- **Recent Tickers strip** lives in the War Room hub header ("Recent: NVDA
  TSLA AAPL"). Persisted in `localStorage` under `risedual:recent-tickers`
  (max 3, deduped, most-recent first). All three War Room subtabs call
  `addRecent(symbol)` on analyze success, so both manual searches and
  deep-link arrivals populate it. Click a pill → re-dispatches
  `risedualai-warroom` so the active subtab re-runs without any typing.
- Utility: `/app/frontend/src/utils/recentTickers.js` — exports `addRecent`,
  `getRecent`, `subscribeRecent` (pub/sub so the hub re-renders instantly).
- **Verified E2E**: clicked XLK tile → War Room opened → XLK auto-analyzed
  → Recent strip showed NVDA/TSLA/AAPL pills from localStorage in one render.

### CTR Breakdown By Hub + Full War Room Ticker Broadcast (COMPLETED Feb 19, 2026)
- **Backend**: `GET /api/analytics/chip-events/stats` now also returns
  `by_hub[]` aggregated per `context_hub` with `shown`, `clicked`, `l1_ctr`,
  `action_shown`, `action_clicked`, `l2_ctr`, sorted by total event volume.
- **Admin panel**: "Per-Hub Breakdown" table added under Chip CTR tab — shows
  which app surfaces (Dashboard / Research / War Room / Options / Workspace)
  drive the highest L1 (chat chip) and L2 (deep-link action) CTR. Green when
  CTR ≥20%, amber 10–19%, slate <10%.
- **Ticker-broadcast pattern extended**: `MarketPrediction` and
  `AIHypothesis` now both listen to the `risedualai-warroom` event, mirroring
  `AIWarRoom`. Any deep-link navigation with `{view:'warroom', subTab:'X'}`
  + a ticker dispatch auto-fills the ticker and runs analysis on whichever
  subtab is landed on.
- **WarRoomHub reactive sync**: previously only read `initialTab` on mount —
  now syncs via `useEffect`, so repeat-clicking different War Room deep-links
  while already on the hub switches the sub-tab correctly.
- **Verified E2E**: clicked SM Shift Alert → War Room Hypothesis subtab →
  TSLA auto-filled → "GPT-5.2 is analyzing TSLA..." started. Per-Hub
  Breakdown table shows live aggregated data across 3+ hubs.

### Smart Money Board → War Room Deep-Link (COMPLETED Feb 19, 2026)
- Every Smart Money Shift Alert row in `Watchlist.jsx` now has a compact
  "WAR ROOM →" button (and per-row `Swords` icon for SM-scored rows).
- Click flow: (1) logs `action-clicked` telemetry with chip_text
  `"Open {TICKER} War Room (SM Board|SM Shift Alert)"`, (2) dispatches
  `risedualai-navigate` → War Room hub, (3) dispatches `risedualai-warroom`
  with the ticker.
- `AIWarRoom.jsx` listens to `risedualai-warroom`, populates the symbol input,
  and auto-fires `analyze()` so the Strategist vs. Auditor run starts in one
  click — zero keystrokes between "I see a Smart Money shift" and "I have AI
  verdict."
- Verified E2E: clicked NVDA shift alert → War Room hub rendered → NVDA input
  auto-filled → "Deploying War Room for NVDA" analysis auto-started.

### Floating Chat Window (COMPLETED Feb 19, 2026)
- Un-pinned the chat from screen edges on both breakpoints.
- Desktop (≥lg): 400×560 floating card, ~24px from bottom-right, backdrop-blur
  + elevated shadow.
- Mobile: ~12px side margins × 72dvh height above the bottom nav — no longer
  a full-screen takeover; navbar/ticker remain visible.

### Level-2 AI Chat Actions — Inline Deep-Link Buttons (COMPLETED Feb 19, 2026)
- **Backend**: `/api/chat/followups` now returns `{chips[], actions[]}`. The LLM
  picks 0–2 deep-link actions when the reply has clear routing intent (ticker
  discussed → research/watchlist; predictions → warroom; options chains →
  options; portfolio/P&L → workspace). Labels capped at 40 chars, tickers validated.
- **Backend**: `/api/analytics/chip-event` now accepts `action-shown` /
  `action-clicked` in addition to `shown` / `clicked`, so L2 adoption is tracked
  independently from L1 chips.
- **Backend**: `/api/analytics/chip-events/stats` now returns `action_shown`,
  `action_clicked`, `action_ctr`, `top_actions[]` alongside the existing chip
  stats.
- **Frontend**: New event bus `risedualai-navigate` (listened in `App.js`) lets
  any surface deep-link into a hub via `{view, subTab}` payload. The chat
  uses it for L2 clicks.
- **Frontend**: Chat UI renders a cyan "Go" row of action buttons (with `→`
  suffix) ABOVE the gray "Next" row of L1 chips. Clicking an action:
  1. Logs `action-clicked` telemetry.
  2. Dispatches the correct nav event (`risedualai-navigate` for hubs;
     `risedualai-research`/`risedualai-add-watchlist` for ticker-aware actions).
  3. Closes the chat so the target hub becomes visible.
- **Admin**: Chip CTR tab upgraded to a 5-KPI grid (Shown · Clicked · L1 CTR ·
  L2 CTR amber · Signal) plus a second table for top-clicked deep-link actions.
- **Verified**: 17/17 backend tests passed; E2E chat flow + admin panel verified
  by the testing agent (iteration_135).

### Chip Adoption Admin Dashboard (COMPLETED Feb 19, 2026)
- **New component** `/app/frontend/src/components/admin/ChipAdoptionInsights.jsx`
  mirrors the `HelpSearchInsights` pattern: 4 KPI cards (Shown · Clicked · CTR ·
  Level-2 Signal), 7d/30d/90d window toggle, top-clicked chips table.
- **Wired into AdminPanel** as a new tab `Chip CTR` (icon: MessageSquare) between
  `Help Search` and `Tools`.
- **Signal thresholds**: &ge;20% CTR = `High` (ship Level-2 deep-links),
  10–19% = `Medium`, &lt;10% = `Low` (redesign before investing).
- **E2E verified**: logged in as admin, opened panel, clicked `Chip CTR` tab,
  confirmed all KPIs + top-clicked table populate from real Mongo events.

### Level-1 AI Chat Follow-up Chips + Adoption Telemetry (COMPLETED Feb 19, 2026)
- **Backend** `POST /api/chat/followups` (Emergent LLM, `gpt-4o-mini`) generates 3
  contextual follow-up suggestions after every assistant reply.
- **Backend** `POST /api/analytics/chip-event` logs `shown` + `clicked` events to
  `chip_events` collection (non-blocking, accepts anon + authed users).
- **Backend** `GET /api/analytics/chip-events/stats?days=N` — admin-only; returns
  shown/clicked counts, CTR, top clicked chips.
- **Frontend**:
  * `RiseDualGPTChat.jsx` fires `clicked` telemetry in `onFollowupClick` before
    dispatching the prefill → sendMessage flow.
  * `ChatComponents.jsx` fires `shown` telemetry via a `useEffect` + `Set` ref
    (dedupe by `msgIdx::chipText`) so each rendered chip is counted exactly once.
  * Both calls are fire-and-forget (silent on failure).
- **Purpose**: CTR from this loop gates the decision to build Level-2 inline
  deep-link action buttons. Low CTR → skip Level-2; high CTR → invest.

### Help Search Weekly Digest — Proactive Admin Push (COMPLETED Feb 18, 2026)
- **New service** `/app/backend/services/help_search_digest.py`:
  - Aggregates last-7-days `help_search_events` via $group pipeline.
  - Skip threshold: <3 zero-result events → no admin spam on quiet weeks.
  - Only sends to active `admin`/`owner` roles with email (excludes merged/deactivated).
- **New HTML template** `_help_search_digest_html` in `email_service.py`:
  - KPI row (Total · Zero-Result · Gap Signal Low/Medium/High by rate).
  - "Biggest Gap This Week" callout with top query + user count.
  - Top-15 table: query, count, unique users, context hubs.
  - Light-theme Gmail-safe layout consistent with digest emails.
- **APScheduler job** registered in `server.py` — `cron` Mon 7:00 UTC, id=`help_search_weekly_digest`.
- **Manual trigger** `POST /api/analytics/help-search/send-digest` (admin-only) + "Email digest" button in the admin panel Help Search tab.
- **Bug fix**: `seed_admin()` in `routes/auth.py` now respects merged state. Previously every restart resurrected `managingdirector@redslateholdings.com` to `role=owner, is_active=True` — now checks for `role=='merged'` or `merged_into_email` first, only updates password hash for audit access.

## Help Center v2 (COMPLETED Feb 18, 2026)
- **Rewrote `/app/frontend/src/components/HelpCenter.jsx`** to match v2 aesthetic:
  - Uses the same `IconTabBar` component as War Room / Research / Options / Workspace.
  - 8 sections × ~45 tips, all content refreshed for v2 architecture (War Room hub, Stock Detail merge, Classic UI toggle, paper trading gates, referral rewards, live broker status).
  - Global fuzzy search across all titles + content with score-ranked results.
  - "Take me there →" deep-links that close the modal and navigate to the exact hub/sub-tab.
  - Context-aware: receives `activeView` from App so search events are tagged with the hub the user was viewing.
- **Search telemetry** — debounced (700ms) POST to `POST /api/analytics/help-search` on every non-trivial query; fire-and-forget.
- **Admin panel "Help Search" tab** (`/app/frontend/src/components/admin/HelpSearchInsights.jsx`):
  - 3 KPI cards: Total events, Zero-Result events, Gap Signal (Low/Medium/High based on zero-result rate).
  - Top zero-result queries table: count, context hub, last-seen timestamp.
  - Top queries overall with avg results per query.
  - 7d / 30d / 90d window toggle.
- **Backend endpoints** (`/app/backend/routes/analytics.py`):
  - `POST /api/analytics/help-search` — logs `{q, results_count, context_hub, user_id, is_anon, ts}` to new `help_search_events` collection. Skips queries <2 or >120 chars.
  - `GET /api/analytics/help-search/stats?days=N&limit=N` — admin-only; aggregates via $group pipeline; returns zero_result_top + top_queries + overall stats.

## v2 UI Consolidation (COMPLETED Feb 18, 2026)
1. **War Room hub** — 5th top-level nav between Dashboard and Research (orange accent).
   Merges Adversarial AI + Predictions + Hypothesis + Signals + Intelligence (5 → 1).
2. **Stock Detail hub** — shared ticker input dispatches `risedualai-research` event.
   Company + StockFit + 13F Holders all respond to the same search (3 → 1).
3. **IconTabBar** reusable component — icon-only tabs + native hover tooltip + ⓘ legend
   popover + active-tab breadcrumb. Used by War Room, Research, Options, Workspace.
4. **Slim mobile menu** — 20 buttons → 7 (hubs + Admin + Logout). All sub-tabs live
   inside each hub's icon bar now, no duplication in the menu.
5. **v2 promoted to DEFAULT** (previously opt-in `?v2=1`). Classic UI preserved as
   one-click archive (`?v1=1` or "Classic UI" pill in header).

### Admin + Account Management — NEW
* **Admin panel "0 users" bug fixed** — `role=admin` accounts can now access
  `/api/auth/admin/users` (previously owner-only). `require_admin()` helper added.
* **Red Slate owner account merged into `admin@risedual.ai`**:
  * 108 docs re-pointed (73 predictions, 24 credit events, 8 chat sessions, 2 smart
    orders, 1 trading bot).
  * Empty duplicate singletons discarded (paper_portfolios, user_credits,
    chat_memory_prefs, watchlists).
  * `referral_codes` conflict tagged `_merged_...` for manual review.
  * `admin@risedual.ai` promoted to `role=owner`.
  * Red Slate shell kept deactivated (`role=merged`, `is_active=false`) for audit.
  * Kraken LIVE connection + 13 api_keys + 32 paper trades + 6 paper positions
    already under admin — preserved intact.

### Email System — REWRITTEN
* `_base_html` light-theme template (Gmail/Outlook-safe with `bgcolor` attrs).
* 8 template variants rebuilt with dark text on light backgrounds + preheader.
* `digest_service.py` rewritten to pull real market data from:
  * `prediction_cache` (market_overview narrative)
  * `predictions` (top AI predictions, last 48h)
  * `smart_money_scores` (institutional flow)
  * `sec_13f_alerts` (regime shifts, last 7d)
* Test accounts (`@test.com`, `test_*@`, `emailtest*`) excluded from sends.
* Pacing added (250 ms/send) to respect Resend's 5 req/sec limit.
* Tiered reward emails wired into `scan_hit_threshold_rewards` and
  `scan_monthly_leaderboard_rewards`.

### Previous Session Work (preserved)
* SEC 13F holder tracking + regime-shift alerts (via EDGAR direct scraping)
* OpenFIGI CUSIP → ticker mapping w/ MongoDB caching
* Watchlist Smart Money Score badges + 30-day sparklines
* "Share My Smart Money Board" PNG export (html2canvas + QR code)
* Tiered Referral Rewards + top-5 Leaderboard
* ML Pipeline (risedual_core), Thread-Safe Multi-Agent Engine
* VAPID push notifications

## 5. Known Issues / Limitations
* QuiverQuant endpoints return 500 — blocked on external provider (P2).
* Alpaca LIVE Client ID/Secret pending user submission — paper works, live gated.

## 6. Backlog / Roadmap
### P2 — Upcoming
* Accumulate 30 live paper trading days to unlock ML Tier 3.

### Nice-to-have
* Thinkorswim-style "Terminal Mode" workspace route (dockable panels, ticker tape,
  monospace density).
* One-click "Send me a fresh digest" button in the Pro dashboard for on-demand
  digest preview.
* Admin panel redundancy cleanup (user flagged duplicate buttons in menus — mobile
  menu done; admin panel itself still pending review).

## 7. Test Credentials
See `/app/memory/test_credentials.md`.


## 7b. Deployment Journal

Running log of what's shipped vs. queued lives in
`/app/memory/DEPLOYMENT_NOTES.md`. Agents must append to the "Queued for
next deploy" section at the end of every meaningful change. When the user
deploys, they run `/app/scripts/mark-deployed.sh "label"` to snapshot the
queue into a timestamped "Shipped" block.


## 8. Changelog

### 2026-04-22 — Date-rendering fix: 27× `datetime.utcnow()` → `datetime.now(timezone.utc)`
* User reported emails showing timestamps "all over the place". Root
  cause: 27 calls to `datetime.utcnow().isoformat()` across 4
  scraping services returned naive ISO strings with no `+00:00`
  suffix; email clients rendered them in recipient local time,
  producing inconsistent dates across recipients.
* Mechanical sweep closed all 27 sites in
  `market_prediction_service`, `real_estate_scraping_service`,
  `crypto_scraping_service`, and `financial_scraping_service`.
* Live verification: insider-trade timestamps now end with
  `+00:00`. Zero remaining `utcnow()` calls in non-test backend.

### 2026-04-22 — P2 RESOLVED: QuiverQuant + insider scraper
* **Root cause reframed**: not flakiness. Quiver's
  `beta/historical/{endpoint}/{ticker}` routes have been 500-ing
  across the board for weeks; `beta/live/*` (full feed) routes
  work. Migrated four public getters to a cached live-feed +
  client-side ticker filter fallback. Added two-tier fallback on
  `govcontracts` (detailed `-all` → aggregated).
* **Scraper bug fix**: OpenInsider scraper was hitting the wrong
  URL (filter-form shell, no results) and reading wrong column
  indices (insider = filing date, trade_type = title). New canon:
  `latest-insider-sales-of-1m` URL, 13-col mapping verified
  against live HTML.
* **Route shape fix**: `/api/market/insider-trades` response type
  corrected from `dict[str, Any]` to `list[dict[str, Any]]` —
  pre-existing serializer mismatch was causing blanket 500s.
* **Circuit breaker hygiene**: 404s no longer trip the breaker
  (they're path errors, not server errors). Log placeholders
  `<endpoint_key>` / `<expr>` were interpolated for real.
* **7 new regression tests** in `test_quiver_fallback.py`.

### 2026-02-20 — Code-review cleanup
* **`alert_dedup.py`** — swapped `hashlib.md5` → `hashlib.sha256`
  for alert-ID hashing. Collision risk was a non-issue either way;
  change is hygiene. SHA-256 hex is 64 chars vs MD5's 32, so
  in-flight dedup rows from the MD5 era won't collide with new
  SHA-256 rows (at most one extra alert per stale row during
  cut-over).
* **3 empty `catch {}` blocks** — `useReferralCapture.js`,
  `ChatInput.jsx` precheck fetch, `TerminalModeHub.jsx` headlines
  poll — all now call `logger.warn(...)` via the dev-only
  `utils/logger.js`. Prod stays quiet, local debugging gains a
  diagnostic line.

### 2026-02-20 — Global kill switch + `safe_gather` helper
* **New module** `ai_core/kill_switch.py` — thread-safe fleet-wide
  circuit breaker. Trips on ≥25% drawdown OR ≥30% rolling error
  rate (min 5 samples). 5-min auto-clearing cooldown. Env-tunable
  thresholds. Module-level singleton + `guarded_execute()` wrapper
  that accepts both sync and async callables, records broker-style
  `{"error": ...}` failures, and exempts `CancelledError`.
* **Hook** in `trading_bot_service.execute_signal()` at a new step 0
  — guard fires before any sizing math, short-circuits with a
  `cooldown_remaining_seconds` payload when active. Outcome
  recorded into the rolling window after the broker call.
* **Admin endpoints** — `GET /api/admin/kill-switch` (status) and
  `POST /api/admin/kill-switch/reset` (owner-only force-clear that
  wipes both flag and error window).
* **New helper** `services/structured_log.safe_gather()` — paired-
  fallbacks wrapper over `asyncio.gather(return_exceptions=True)` +
  `unwrap_gather_result` loop. Auto-tags per-task failures with
  `note=task_N_failure`.
* **Tests** — 14 kill-switch tests + 3 safe_gather tests + 2 new
  integration tests on `execute_signal`. Autouse conftest fixture
  resets the singleton between tests to prevent cross-test
  contamination. All 120 tests green.

### 2026-02-20 — Admin UI `GatherErrorStrip` card
* **New** `frontend/src/components/admin/GatherErrorStrip.jsx` — heat-
  stripe card that renders the `/api/admin/gather-error-rate` payload
  inside the admin Conviction tab, directly below `MLHealthStrip`.
  Headline count + `1h/6h/24h` window selector + per-context row
  stripes + tone that flips to amber when any one context hits ≥40%
  share of the window's errors.
* **Wiring** — `ConvictionCalibration.jsx` imports + mounts the new
  card. Zero changes to existing ML strip or bucket grid.

### 2026-02-20 — `/api/admin/gather-error-rate` observability tile
* **New rolling-counter module** `services/error_metrics.py` — thread-
  safe bounded deque (MAX_EVENTS=10k, ~1 MB cap). Every `log_error`
  call now pushes a `{ts, context, type, note, extra}` record.
  In-process by design; no Mongo writes on the hot path.
* **New endpoint** `GET /api/admin/gather-error-rate?hours=24&context_prefix=...`
  groups events by `context`, returning total count, top 3 exception
  types, and most-recent timestamp per group. Answers "which
  provider is flaking right now" without needing log-aggregator
  access.
* **Safety:** the metric hook is wrapped in `try/except: pass` — a
  buffer failure can never block log emission. ERROR-only capture;
  WARNING / INFO are not counted.

### 2026-02-20 — `market_data_service` migrated to `unwrap_gather_result`
* **Completes the `asyncio.gather` guard migration started in the prior
  session.** Both `get_ticker_data()` and `get_crypto_data()` now use
  `unwrap_gather_result` from `services.structured_log`, matching the
  war_room / fred / crew_engine pattern. `CancelledError` stays silent,
  `Exception`s now emit a structured `log_error` line (previously
  discarded); ticker path tagged `context=market_data.ticker`, crypto
  tagged `context=market_data.crypto`.
* **New test** `tests/test_market_data_gather_guard.py` (2 tests, both
  passing) pins the three-tier guard for both paths.
* **mypy gate** holds steady at baseline 69 errors (explicit
  `list[dict]` annotations added for `ticker_data` / `crypto_data`).

### 2026-04-19 — Sliding-TTL price cache + prediction dedup
* **New service `services/sliding_cache.py`.** Thread-safe, process-local,
  O(1) get/set with sliding TTL — each access resets expiry. Shared across
  sync + async price-provider entrypoints so rapid pulls on the same symbol
  never double-fetch upstream.
* **`price_provider` wired to sliding cache.** All five entrypoints use it:
  `get_quote`, `get_quote_sync`, `get_crypto_quote`, `get_crypto_quote_sync`
  (5 min TTL), and `get_daily_history` / `get_daily_history_sync` (30 min TTL).
  First miss hits upstream (~250 ms); repeat hits return in ~0 ms with
  `source="<provider>:hot"` suffix. MongoDB persistent cache retained for
  cross-restart warm-up.
* **Reset caps on both sliding mechanisms.**
  - `SlidingCache` now supports `max_resets`; the shared `price_cache`
    singleton is configured with `max_resets=2` (5 min TTL × 3 touches =
    ~15 min max lifetime). Past the cap, reads still return the cached
    value but stop extending — the entry ages out and forces a fresh
    upstream fetch.
  - `log_prediction` caps `dedup_count` at `MAX_DEDUP_HITS=1`
    (15 min TTL × 2 touches = ~30 min max sliding lifetime). A third
    identical firing after the cap creates a **new** prediction record
    and verifies against the current price — catching drift that a
    perpetually-sticky signal would otherwise hide.
* **Data cleanup.** Dropped 49 duplicate SPY@$679.46 NEUTRAL predictions
  left over from a previous session's runaway logger.

### 2026-04-19 — Dynamic NEUTRAL tolerance + Live bot execution wiring
* **Dynamic per-symbol NEUTRAL tolerance.** Replaced flat 5% (1w) / 2% (24h) bands
  with ATR-based adaptive bands in `services/prediction_tracker.py`:
  `tolerance = 1.5 × 10-day-ATR%` (24h) or `3 × ATR%` (1w), clamped to [2%, 10%].
  Cached 12h per symbol.
* **Retro rescore endpoint.** `POST /api/accuracy/rescore-neutral?window={24h|1w|both}`
  (owner-only). Persists `neutral_tolerance_used` + `rescored_at` on each touched record.
* **Live bot execution.** `services/trading_bot_service._execute_bot_trade` now
  routes `mode="live"` through `routes.broker._get_or_refresh_client` →
  `client.place_order()`. Grid, Signal, and Webhook bots all execute live.

### 2026-02-18 — Watchlist.jsx refactor complete
* `Watchlist.jsx` reduced from 447-line monolith to 58-line orchestrator.
* Logic extracted to `/app/frontend/src/hooks/useWatchlistData.js` (all fetches,
  localStorage persistence, backend sync, 60s quote refresh, SMS score/history,
  external `risedualai-add-watchlist` event listener).
* UI split across `/app/frontend/src/components/watchlist/`:
  - `WatchlistToolbar.jsx` (header, expand/collapse, add input, cap warning, share/refresh)
  - `SmartMoneyShiftAlerts.jsx` (score-shift alert rows w/ chat prefill + War Room deep-link)
  - `WatchlistTable.jsx` (row rendering + sparkline + Smart Money pill + actions)
* Lint: 0 issues. Smoke test: collapsed + expanded states render, 5 rows + 2 SMS
  shift alerts visible for admin account.
* `managingdirector@redslateholdings.com` marked DEACTIVATED in test_credentials.md
  (do not re-enable).


### 2026-02-18 — Social share / OG preview per ticker
* **Backend**: `GET /api/share/{ticker}` returns a server-rendered HTML page
  with full OpenGraph + Twitter Card + JSON-LD `FinancialProduct` metadata,
  live quote-enriched title (`AAPL · RISEDUAL AI — AI War Room — $270.23 ▲2.59%`)
  and description. Meta-refresh + JS redirect bounces real browsers to
  `/?warroom=TICKER`. Honors `X-Forwarded-Proto`/`X-Forwarded-Host` so canonical
  URL + SPA redirect use the public domain (not cluster-internal). HEAD supported
  for preview crawlers that probe before GET.
* **Frontend**: `App.js` intercepts `?warroom=TICKER` query param on load and
  dispatches the same nav+warroom events `deepLink.js` uses — SPA auto-opens the
  AI War Room with the ticker queued for analysis. URL is cleaned via
  `history.replaceState` so manual reloads don't re-fire.
* **Share button**: Added to `WarRoomHub.jsx` header (next to the v2 badge).
  Label reflects current ticker ("Share AAPL"). Uses Web Share API on mobile
  (native X / iMessage / WhatsApp / Mail / Slack / Signal sheet) and falls back
  to clipboard + toast on desktop.
* **Platform coverage** (via standard OG + Twitter tags):
  X, Facebook, LinkedIn, WhatsApp, iMessage, Slack, Discord, Telegram, Reddit,
  Bluesky, Pinterest, Signal, Teams — plus Google rich results via JSON-LD.
* Smoke-tested with `facebookexternalhit`, `Twitterbot`, `LinkedInBot` User-Agents
  — all receive correct meta tags. Live redirect test: share URL → SPA → War
  Room opens with ticker auto-analyzed. 0 lint issues.

### 2026-02-18 — Onboarding tour positioning + share endpoint tests
* **Tour polish**: `OnboardingTour.jsx` — centered steps now pin to the top of
  the viewport (top: 80px, horizontally centered) instead of blocking the
  middle of the screen. Card is translucent (`bg-[#0B1426]/85 backdrop-blur-md`),
  scrim reduced from 70% → 25% black, so users can actually see what's being
  tour-ed while the tooltip guides them.
* **Share endpoint tests**: `/app/backend/tests/test_share_endpoint.py` —
  11-test pytest suite covering: 200 HTML response, all required OG tags,
  Twitter Card tags, JSON-LD FinancialProduct, ticker case normalisation,

### 2026-02-18 — Share link referral attribution
* **Backend**: `/api/share/{ticker}` accepts optional `?ref=CODE` query param
  (alphanumeric + dash, 1-32 chars, XSS-sanitised). Preserved through the SPA
  redirect URL as `/?warroom=TICKER&ref=CODE`.
* **Frontend App.js**: `?warroom=` handler now preserves the `?ref=` param
  instead of stripping it, so `useReferralCapture` + AuthModal pick it up at
  signup for credit attribution.
* **Frontend WarRoomHub.jsx**: lazily fetches the authenticated user's
  referral code via `/api/referral/info` and appends `?ref=CODE` to every
  copied share URL. Every Pro user becomes a passive growth engine — click on
  their shared analysis → visitor signs up → referrer credited automatically.
* **Tests**: +5 new pytest cases in `test_share_endpoint.py` for ref
  preservation, sanitisation, dash-prefixed codes, overlong rejection, and
  default-no-ref behaviour. **16/16 pass.**

  SPA redirect, input sanitisation (overlong + special chars), X-Forwarded-Host
  handling, cluster-internal host fallback, HEAD method support. **11/11 pass**.


### 2026-02-18 — Terminal Mode, OG PNG generator, Share ROI badge
* **Terminal Mode workspace** (`/app/frontend/src/components/hubs/TerminalModeHub.jsx`):
  Thinkorswim-inspired 2×2 dockable grid (Watchlist / Market Signals / War Room
  deep-link hints / Headlines stream). Drag splitters reapportion layout;
  positions persist to localStorage. Monospace typography (JetBrains Mono),
  tight density. Header includes live SPY/QQQ/IWM/VIX pulse + ET session clock.
  Added to Tools menu (`nav-terminal-btn`) and reachable via
  `navigateTo('terminal')` event. Lazy-loaded for fast initial paint.
* **Dynamic OG PNG generator** (`/api/share/img/{ticker}.png`): 1200×630 PNG
  composited server-side with Pillow — brand gradient background, huge
  monospace ticker, live price, colored % change (lime up / orange down), teal
  "Open War Room →" pill CTA. 60s in-memory cache keyed by ticker+price-bucket.
  Share HTML now points `og:image` + `twitter:image` at this endpoint instead
  of the static logo — every share on X/Slack/LinkedIn/WhatsApp/Discord now
  renders a bespoke live-price card.
* **Share button ROI badge** (`WarRoomHub.jsx`): Share button lazy-fetches
  `/api/referral/info` and surfaces the authenticated user's `completed_referrals`
  as a lime pill badge on the button (e.g. "Share AAPL · 3"). Tooltip reads
  "3 signups via your links so far" — turning a one-off action into a habit
  loop by giving the user continuous social-proof feedback on their shares.
* **Regression check**: `test_share_endpoint.py` 16/16 pass. All key endpoints
  (ready, quote, sectors, share HTML, share PNG, share with ref, referral
  leaderboard) return 200 + correct payloads.
* Lint: 0 issues across all new/modified files.

### 2026-02-18 — On-demand digest "Send me one now" button
* **Backend**: `POST /api/digest/send-now` — authed users trigger an immediate
  personalized digest delivery to their own inbox. Rate-limited to 1/hour via
  a `last_on_demand_digest_at` timestamp on the user doc. Bypasses the opt-out
  flag because the request is explicit. Returns content summary (predictions,
  smart-money, alerts, watchlist-intel) so the UI can toast-display it.
* **Digest service**: extracted `send_digest_to_user(db, user)` helper (reuses
  `collect_digest_data` + `build_digest_html` + `_routed_send`) so
  single-user sends don't duplicate logic from the scheduled job.
* **Frontend** (`UserWorkspace.jsx`): new teal "Send me one now" button next
  to the existing subscribe/unsubscribe toggle. Shows spinner while sending,
  `sonner` toast on success ("Fresh digest is on its way — 5 predictions, 6
  smart-money alerts, 2 market alerts"), handles 429 gracefully, and a
  subtitle clarifies the button exists ("Morning briefing at 6:00 AM UTC ·
  on-demand preview available").
* **Tested**: Live API returns 200 on first call (admin@risedual.ai received
  digest with 5 predictions + 6 smart-money + 2 alerts + overview), 429 on
  second with clean retry-after message.



### 2026-02-18 — Admin Panel cleanup
* `AdminPanel.jsx`: full refactor to eliminate duplicate/misleading controls
  and improve scanability with 12 tabs.
* **Context-aware header** — title shows `Admin · {TabLabel}` and subtitle
  adapts per tab (`50 users total` on Users, `MongoDB cache tiers & TTLs` on
  Cache, `Market-data + email failover health` on Providers, etc.). Was
  always-stale `{users.length} users total`.
* **Scoped Refresh button** — the header's RefreshCw only appears on the
  Users tab, where it actually refreshes the user list. On Cache / KeyVault /
  ChipAdoption / HelpSearch / etc., each tab already has its own context-
  specific Refresh button, so the header one was a misleading no-op.
* **Proper close button** — literal "x" character replaced with lucide `X`
  icon in a rounded hover button. `data-testid="admin-close-btn"` for tests.
* **Grouped tabs** — 12 tabs now organised under three subtle group labels
  (PEOPLE · OPERATIONS · INSIGHTS) with thin dividers in the tab bar. No
  behavioural change, just scanability.
* **Replaced nested ternary** — 12-way `tab === 'x' ? <X/> : tab === 'y' ? …`
  cascade replaced with `TAB_COMPONENTS` lookup object. ~40 lines shorter,
  trivially extensible.
* Lint: 0 issues. Live-verified with screenshots — tab switching updates
  header title, subtitle, and refresh-button visibility correctly.

### 2026-02-18 — Mobile: Connect Broker entry-point
* **Bug**: `<BrokerConnect />` was rendered only inside the desktop-only
  `<div className="hidden lg:flex">` block of Navbar, so the Connect Broker
  button was completely absent on mobile — users couldn't wire Alpaca/Kraken
  from their phones. Reported by user on deployed site where they needed to
  re-paste Kraken keys from mobile.
* **Fix**: added a window-event handshake — `BrokerConnect` listens for
  `risedualai-open-broker-connect` and opens its own modal. Added a prominent
  teal "Connect Broker" chip in the MobileMenu utility row (next to Admin + Help)
  that dispatches the event. Clean, zero-duplication — BrokerConnect's own
  state + portal modal handle the rest.
* **Verified**: mobile viewport (414×896) → hamburger → Connect Broker chip →
  Broker modal opens with all brokers listed. 0 lint issues.




### 2026-02-18 — Removed Red Slate deactivated account (root cause of READ ONLY bug)
* **Bug**: deployed site showed broker "READ ONLY" for `admin@risedual.ai`
  because `_is_execution_allowed()` required `role == "owner"`, but the seed
  logic had created that account with `role: "admin"`. The `owner` role was
  assigned only to `managingdirector@redslateholdings.com`, which the user
  deactivated in Feb 2026 — leaving production with no live `owner` account
  and every broker connection locked to read-only.
* **Fix**:
  - `/app/backend/.env`: `OWNER_EMAIL=admin@risedual.ai`, removed now-unused
    `ADMIN_EMAIL` / `ADMIN_PASSWORD` vars.
  - `/app/backend/routes/auth.py` `seed_admin()`: consolidated to a single
    owner seed. On every startup, promotes `admin@risedual.ai` to
    `role: owner, is_active: True`. Added one-shot cleanup that deletes any
    remaining Red Slate row with `role in [merged, free]` — no more
    resurrection, no more confusion.
* **Verified on preview**: Red Slate row deleted, `admin@risedual.ai`
  role=owner, is_active=True, and `GET /api/broker/execution-status` returns
  `{execution_allowed: true, mode: "live"}`.
* **On production deploy**: seed cleanup runs automatically → Red Slate row
  deleted → `admin@risedual.ai` promoted to `owner` → broker flips from
  READ ONLY → LIVE TRADING with no manual intervention.


### 2026-02-18 — Code review triage & genuine cleanups
* External code review flagged 200+ findings; auditing them surfaced that the
  "critical" items (eval/exec, 18 undefined vars, hardcoded secrets) are all
  **false positives** from a context-blind static scanner:
  - "eval() in backtester_service.py:193" → line is a comment announcing the
    AST-based safe evaluator that already replaced eval.
  - "eval/exec in test_iteration36" → security tests that verify the evaluator
    REJECTS eval/exec strings (intentionally split `"ev"+"al"`).
  - "18 undefined variables" → `pyflakes .` returns empty.
  - "Hardcoded secrets" → mostly env-var NAMES (`"RESEND_API_KEY="` searched
    inside .env) or dev-only preview passwords from test_credentials.md.
* **Genuine cleanups performed**:
  - `tests/conftest_creds.py`: consolidated — `OWNER_EMAIL` now aliases to
    `ADMIN_EMAIL` (both point to `admin@risedual.ai`) after Red Slate removal.
  - `tests/test_iteration134/135`: moved to `os.getenv()` + safe defaults.
  - `tests/test_iteration135`: removed dead `owner_session` fixture
    (referenced deleted Red Slate account, never consumed).
  - `tests/test_iteration42`: updated stale assertion to pass after Red Slate
    cleanup.
  - `utils/deepLink.js` + `utils/recentTickers.js`: replaced 6 empty
    `/* silent */` catch blocks with `console.debug()` so real failures are
    still observable.
* **Deferred to post-launch** (refactoring risk vs reward): 207 hook-dependency
  warnings (~70% false positive), AppContent/Navbar component splits,
  localStorage "encryption" (already non-sensitive), 544 `is` vs `==` lint
  nits in tests, type-hint coverage, inline-prop useMemo micro-perf.
* Regression: `test_share_endpoint.py` 16/16 pass. Lint: 0 issues.
