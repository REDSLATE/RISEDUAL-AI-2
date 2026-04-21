# RISEDUAL AI — Deployment Notes

> **What this file is:** a running journal of every code change, split into
> "Shipped to production" vs. "Queued for next deploy". Whenever you ask
> "what changed since my last deploy?", this file is the answer.
>
> **How it stays current:**
> 1. The agent (me) appends entries to the "Queued for next deploy" section
>    at the end of every change it makes.
> 2. When you deploy, run `/app/scripts/mark-deployed.sh "short label"` —
>    that moves every queued entry into a new "Shipped" block, timestamped
>    with the commit hash that was live at the moment you ran it.
> 3. Nothing here is inferred. Every entry names the files touched, the
>    behavioural impact, and which session introduced it.
>
> **Do NOT edit past "Shipped" blocks by hand** — they're historical record.
> Edits to the "Queued" section are fine; you own that bucket.

---

## 🟡 Queued for next deploy

> Everything below this line has been merged into the main branch on the
> sandbox/preview but has **not** been marked as shipped. Review before
> hitting Deploy.

*Nothing queued. Agent will append here as changes land.*

### 2026-02-19 — Recent-loss bridge + Auditor Feedback Loop
*Session: continued*

Closed the last two architectural gaps identified in the memo review.

**Part 1 — Recent-loss bridge (`routes/risk_calculator.py`)**
- `_compute_risk_context` now counts closed `paper_trades` from the last
  24h with `realized_pnl < 0` and adds them to the `losing_streak` tally.
- Bridges the ~24-hour labeler lag where a trade has closed red but
  `predictions.verified_24h` hasn't caught up yet.
- Sum is capped at `STREAK_LOOKBACK` (10) so a single bad day can't
  multiply the factor.
- New `recent_losses_24h` field exposed in the risk context for
  transparency.

**Part 2 — Auditor Feedback Loop (`services/rejection_log.py` + orchestrator hook)**
- New `compute_rejection_bias(days, min_samples, min_rate)`: aggregates
  rejections over the last N days per `(asset, direction)` and computes
  rejection rate = rejections/attempts. Flags pairs where rate ≥ 70% AND
  attempts ≥ 10 (configurable). Returns dominant source + reason.
- New `get_flagged_pairs()` helper returns the set of flagged
  `(asset, direction)` tuples with a 15-minute in-process cache so the
  orchestrator hook stays O(1).
- New admin endpoint `GET /api/admin/rejections/bias` with tunable
  `days/min_samples/min_rate` query params.
- New orchestrator hook (`ml_orchestrator.run_post_signal_pipeline`) as
  **step 0**, before model + gate checks: if the current
  `(ticker, direction)` is flagged, early-exit with a structured
  `orchestrator_bias_feedback` rejection logged. Completes the loop —
  past rejections influence future decisions.
- New rejection source `orchestrator_bias_feedback` added to the
  whitelist in `rejection_log.SOURCES`.

**Verified:**
- `/api/admin/rejections/bias?days=7&min_samples=10` → 200 with expected
  shape (`{window_days, min_samples, min_rate, count, flagged[]}`).
- `_compute_risk_context` return now includes `recent_losses_24h` field.
- Self-test 6/6 green, backend running, no regressions.

**Why the flagged list is empty today:** only 2 seeded rejections in
the DB. The loop will light up within a week of real traffic when
rejection rates per `(asset, direction)` cross the 70%/10-sample
thresholds. Thresholds tunable per-request via the endpoint.

### 2026-02-19 — Three risk-system gaps closed (retrainer hook + UI banners + bot gating)
*Session: continued*

Closed all three gaps flagged in the post-snippet review. All additive,
backwards-compatible, and reuse the circuit-breaker + trade-guard helpers
shipped earlier in the session.

**Gap 1 — Retrainer rejection context (`ml_retrain_service.py`)**
- New helper `_collect_rejection_context(db)` summarises `rejected_signals`
  since the last successful retrain (or 24h cold-start fallback) into
  `{since, total, by_source}`.
- Every `training_log` row now carries `rejections_since_last_run`, so
  sudden spikes in tier-locked or auditor-blocked signals are visible
  in the Admin → Developer Tools retrain history. Foundation for
  future hard-negative replay at train time (would require
  features_replay join).

**Gap 2 — UI banners (`RiskCalculator.jsx`)**
- Three new banners rendered above the Affordability warning when the
  corresponding API field is present:
  1. `risk-reduced-banner` (amber) — circuit breaker tripped
  2. `trade-guard-veto-banner` (red) — R:R below min floor
  3. `exploration-active-banner` (violet) — ε-greedy override fired
- Each banner shows the applied vs requested %, the reason, and the
  numeric trigger so the user knows exactly why sizing changed.

**Gap 3 — Bot-trade risk-guard pre-flight (`trading_bot_service.py`)**
- New `_apply_bot_risk_guards(bot, user_id, qty)` called at the top of
  `_execute_bot_trade` — grid, signal, and webhook bots all go through
  it before paper or live execution.
- Reuses `routes.risk_calculator._compute_risk_context` so UI and bots
  read the same streak/drawdown signals.
- When tripped, halves qty (floor=1 share), logs the de-risk to
  `rejected_signals` via `risk_circuit_breaker` source.
- Fails open on any DB lookup error so a Mongo hiccup can't silence
  bots mid-session.

**Verified end-to-end:**
- Gap 1: `_collect_rejection_context` returns 2 rows against live DB.
- Gap 2: ESLint clean, frontend compiled with no errors.
- Gap 3: Running backend response confirms circuit breaker fires on
  admin user's 4-loss streak → bots would halve qty via same factor.
- Self-test 6/6 green.

### 2026-02-19 — Rejected-signal logging (hard-negatives collection)
*Session: continued*

User dropped two `log_rejected` snippets describing a learning-engine that
captures every signal the pipeline drops. Real gap: the existing codebase
silently rejects signals at 4+ places with only `log.info/debug` entries —
no structured record, nothing retrainable, no admin visibility.

**What landed (all additive — zero trading-logic changes):**

1. `backend/services/rejection_log.py` — new service with
   `log_rejected(asset, direction, reason, source, meta, user_id)`.
   Persists to `rejected_signals` Mongo collection with a 90-day TTL
   index so the collection stays lean on its own. `recent_rejections()`
   + `rejection_stats()` helpers for queries. Every call is
   fire-and-forget — logging failures never break the trading pipeline.

2. Wired into 4 rejection sites:
   - `ml_orchestrator.py` — 3 gate bail-outs (no_model, no_calibration,
     all_tiers_locked) each with structured `meta` incl. prediction_id,
     regime, calibration stats.
   - `ai_signal_validator.py` — every auditor verdict != "buy" is now
     captured with the LLM reasoning (truncated to 500 chars), risk
     level, and confidence. Async `create_task` so the merge path stays
     synchronous.

3. `backend/routes/rejections.py` — two admin-only endpoints:
   - `GET /api/admin/rejections` (filters: source, asset, limit up to 500)
   - `GET /api/admin/rejections/stats?hours=N` (aggregate by source)
   Wired through `route_registry.py`; DB injected in `wire_db()`.

**Indexes (idempotent):**
- `logged_at` TTL (90 days)
- `(source, logged_at desc)`
- `(asset, logged_at desc)`

**Verified end-to-end:**
- Seeded 2 rejections via direct service call → persisted OK
- `GET /api/admin/rejections?limit=5` → returned both, newest first
- `GET /api/admin/rejections/stats?hours=24` → `{total:2, by_source:{...}}`
- Unauthenticated call → 401 as expected
- Self-test 6/6 green, no regressions

**Consumer hook for later:** `ml_retrain_service` can now consume the
`rejected_signals` collection as hard-negative training data (tasks that
the system filtered out, which becomes a labeled dataset once their
outcomes get verified).

### 2026-02-19 — Trade guards: R:R floor + guarded ε-greedy exploration
*Session: continued*

Follow-up to the circuit-breaker work above. The user dropped a minimal
`Auditor` snippet (`min_rr=1.5`, `exploration_rate=0.1`) — same "adopt
the idea, not the class" treatment as the RiskManager snippet.

**What landed:**
- `routes/risk_calculator.py` got a new helper
  `_evaluate_trade_guards(...)` and a new `trade_guards` response block
  on both `/calculate` and `/multi-tp`.
- `RiskCalcRequest` and `MultiTpCalcRequest` gained three optional
  fields: `min_rr: Optional[float]`, `explore: bool = False`,
  `mode: str = "paper"`.
- New constants at top of file: `DEFAULT_MIN_RR = 1.5`,
  `EXPLORATION_RATE = 0.10`.

**Rules:**
- Hard R:R floor — if `rr_ratio < effective_min_rr` (user-supplied or
  `DEFAULT_MIN_RR`), response sets `veto: true` with a reason. **Veto is
  advisory** — the endpoint still returns a fully-sized trade so the
  caller (UI or bot) decides whether to honour.
- ε-greedy exploration — fires only when ALL of: `explore=True`,
  `mode="paper"`, circuit breaker inactive, and `random.random() <
  EXPLORATION_RATE`. Can only OVERRIDE a veto (the whole point of
  sampling "trades the rules would normally reject"), never re-veto a
  good trade.
- Every guard decision persisted to new `risk_exploration_log`
  collection for later performance analysis.

**Verified end-to-end via 5 curl scenarios:**
1. Good R:R → no veto  ✅
2. Bad R:R → veto  ✅
3. Bad R:R + `explore=true` + `mode=live` → exploration blocked  ✅
4. Custom `min_rr=3.0` on a 2.5-R:R trade → veto  ✅
5. Exploration during active circuit breaker (streak=4) → blocked  ✅

Self-test 6/6 green, no regressions.

### 2026-02-19 — Risk circuit-breaker (streak + drawdown auto-de-risk)
*Session: continued*

Filled a real gap the user spotted: the existing `/api/risk-calc` was
math-only — it would happily size you up even during a nasty losing
streak. Added a behavioural safety layer that auto-halves the requested
`risk_pct` when either trigger fires.

**Trigger rules (configurable constants at top of `routes/risk_calculator.py`):**
- `LOSING_STREAK_THRESHOLD = 3` — 3+ consecutive wrong verified predictions
- `DRAWDOWN_THRESHOLD = 0.10` — 10%+ down from peak equity
- `RISK_REDUCTION_FACTOR = 0.5` — halve risk_pct when either trips
- `STREAK_LOOKBACK = 10` — only scan last N verified predictions

**Implementation:**
- New async helper `_compute_risk_context(user_id, account_value)` reads
  `predictions.verified_24h.correct` for the streak count and manages a
  running `paper_portfolios.peak_equity` field for drawdown (upserted on
  every call, so no separate scheduler job needed).
- Both `/calculate` and `/multi-tp` endpoints now apply the reduction
  factor to `risk_pct` before sizing, and return a new `risk_adjustment`
  block so the UI can surface the reason:
  ```json
  { "risk_reduced": true, "reduction_factor": 0.5,
    "requested_risk_pct": 2.0, "applied_risk_pct": 1.0,
    "reason": "losing streak: 4 in a row",
    "losing_streak": 4, "current_drawdown_pct": 0.0,
    "peak_equity": 100108.16 }
  ```
- `fixed_dollar` + Kelly sizing methods deliberately bypass the factor
  (they're explicit-intent, not percent-based) but still expose the
  context in the response for UI transparency.

**Verified:** Live API call against admin account returned
`risk_reduced: true · reason: "losing streak: 4 in a row"` — circuit
breaker actually triggered on real prediction history, not a mock.
Self-test 6/6 green.

### 2026-02-19 — requirements.txt deploy blockers fixed
*Session: continued*

User pasted the current `requirements.txt` pre-deploy and I caught three
issues that would have blocked or regressed a fresh-container install:

1. **`-e /tmp/risedual-v4/risedual_core` → `-e ./risedual_core`**
   - `/tmp/` is ephemeral; path doesn't exist on a fresh container.
   - Switched to the repo-relative vendored package at
     `/app/backend/risedual_core/` (which already has a valid
     `pyproject.toml` and was the real source of truth all along).
2. **`instructor==1.15.1` — removed entirely**
   - 1.15.1 requires `openai>=2.0,<3.0`, conflicting with our pinned
     `openai==1.99.9`.
   - Verified zero imports across all backend code and no transitive
     package depends on it. Safe to drop.
3. **`sse-starlette==3.3.4` → `sse-starlette==2.1.3`**
   - 3.x requires `starlette>=0.49`; `fastapi==0.110.1` pins
     `starlette<0.38`. Irreconcilable without upgrading fastapi.
   - 2.1.3 has an unconstrained starlette dep and works with
     starlette 0.37.x. SSE streaming endpoints (chat, orderflow,
     whale radar, stream) all still functional.

**Verified:** `pip install -r requirements.txt` succeeds, backend
RUNNING, `sse_starlette` import OK, self-test 6/6 green.

### 2026-02-19 — AP News RSS URL typo fix
*Session: continued*

**Problem:** User noticed backend was logging `Error scraping AP News RSS:
Failed to parse: https://feeds.a]pnews.com/apnews/topnews` — there's a
stray `]` in the URL (`feeds.a]pnews.com`).

**Fix:** `backend/services/world_events_service.py` line 93 — URL corrected
to `https://feeds.apnews.com/apnews/topnews`.

**Verified:** log now shows the scraper attempting `feeds.apnews.com`
(fails in preview sandbox only because outbound DNS is restricted there —
same issue blocks Reuters feeds in preview; production is unaffected).

### 2026-02-19 — Pro Max checkout + chat-history tz safety
*Session: continued*

Extracted two genuinely useful ideas from a pair of malformed user-supplied
patches (neither applied — both had duplicate git-diff headers; ignored).

**A. Pro Max ($99/mo) checkout path** — missing feature. The landing page
advertises Pro Max but the backend could only check out "Pro" before.
- `backend/services/payment_service.py` — added `TIER_PRICE_MAP =
  {"pro": 55.00, "pro_max": 99.00}` alongside the existing monthly/annual
  constants. `create_checkout_session()` gained an optional `tier`
  argument that wins when supplied; legacy `plan` stays as fallback.
- `backend/routes/subscription.py` — `CheckoutRequest` gained
  `tier: str | None`. Endpoint resolves `tier` first, falls back to
  legacy `plan`. Stores accurate amount + plan label in
  `payment_transactions`.
- Verified end-to-end via curl: `tier=pro_max` → $99 stored;
  legacy `plan=monthly` → $55 stored. Self-test 6/6 green.

**B. Chat-history timestamp safety** — same class of bug as the Security
Audit crash fixed this morning.
- `backend/routes/ai.py` `get_chat_history()` replaced the string-based
  timestamp comparison (`m.get("timestamp", "9999") >= cutoff`) with a
  typed filter that handles both `datetime` (naive + aware) and ISO-8601
  strings (including the `Z` suffix). Rows with unparseable timestamps
  are dropped rather than crash.

**Ignored from the user's patches:**
- Stale Pro monthly $45 → $55 change (already done earlier this session).
- Annual pricing regression ($40.50/mo → matches old PDF copy) — would
  have removed the 10%-off annual; kept current $49.50/mo.
- `scan_for_signals` mock-generator removal — hunks were truncated and
  the mock data is still used by paper/demo flows.
- Unknown edits to auth.py, server.py, digest_service.py, push_service.py,
  world_events_service.py, models/chat.py — hunks empty/unverifiable;
  `auth.py` in particular is explicitly off-limits per user.

**Behavioural impact:** Additive. Backwards-compatible. Pro Max is now
purchasable; chat history is crash-safe across stored-timestamp formats.

### 2026-02-19 — Domain fix: risedual.com → risedual.ai (Stripe/PayPal docs + tests)
*Session: continued*

**Problem:** User spotted stale `risedual.com` references around Stripe and
PayPal redirect URLs. The live payment service builds `success_url` /
`cancel_url` from the request `origin_url` (no hardcoded domain), but the
deployment guide and subscription test fixtures still referenced the old
`.com` domain.

**Fix:**
- `DEPLOYMENT_GUIDE.md` — 9 replacements (`STRIPE_SUCCESS_URL`,
  `STRIPE_CANCEL_URL`, `PAYPAL_RETURN_URL`, `PAYPAL_CANCEL_URL`, custom-
  domain setup steps, DNS propagation-check URLs).
- `backend/tests/test_subscription_plans.py` — 6 replacements in test
  fixtures that POST `origin_url: "https://risedual.com"` to Stripe.

**Verified:** `grep -r risedual\.com /app` now returns zero results.

**Behavioural impact:** None at runtime — live redirect URLs have always
been built from the browser's origin. Docs + tests now match production.

### 2026-02-19 — Self-Test system (3-layer: scheduler + CLI + Admin UI)
*Session: continued*

**What:** Added a first-class self-test that catches the regression classes
we've actually been hit by (tz-naive datetime compare, stale price
literals, missing collections, scheduler job drop-off, env misconfig).

**Components:**
1. `backend/services/self_test_service.py` — pure check functions,
   `run_self_test(db, scheduler)` returns a structured report.
2. `backend/routes/self_test.py` — admin-only `GET|POST
   /api/admin/self-test`.
3. Wired through `route_registry.py` (router + `set_db` + `set_scheduler`).
4. `server.py` `_run_self_test_monitor` cron (every 15m). Appends to
   `/app/memory/HEALTH_LOG.md` only on state change (PASS↔FAIL) or the
   hourly heartbeat to keep the log readable.
5. `scripts/self-test.sh` — CLI pre-deploy check. Uses the canonical
   owner creds by default, env-var overridable. Exit 0 on PASS, 1 on FAIL.
6. `frontend/src/components/admin/SelfTestPanel.jsx` — "Run self-test"
   button with per-check PASS/FAIL table. Mounted at the top of the
   existing Admin → Developer Tools page.

**Checks (6):**
- db_ping — Mongo responds to `ping`
- collections — 5 required collections present (oauth_token_audit treated
  as lazy; not-yet-written is PASS with info note)
- datetime_comparisons — reproduces the 2026-02-19 Security Audit crash
  against up to 20 `login_attempts` rows to catch tz-naive leaks
- env — MONGO_URL, DB_NAME, EMERGENT_LLM_KEY, STRIPE_SECRET_KEY present
- pricing — scans digest_service + payment_service for `$X/month`
  stale literals ($45/$29/$49/$25); skips lines starting with `#`
- scheduler — 5 required cron jobs registered (digest, headlines,
  prediction_prewarm, prediction_labeler, nightly_ml_retrain)

**Verified:** `/app/scripts/self-test.sh` returns `6/6 passed` on the
current preview env. Endpoint responds 200 under admin auth, 401 without.

**Files touched:**
- new: `backend/services/self_test_service.py`
- new: `backend/routes/self_test.py`
- new: `scripts/self-test.sh`
- new: `frontend/src/components/admin/SelfTestPanel.jsx`
- new: `memory/HEALTH_LOG.md`
- edit: `backend/route_registry.py` (router + db wiring)
- edit: `backend/server.py` (scheduler job + runner + scheduler ref wire)
- edit: `frontend/src/components/admin/AdminTools.jsx` (mount panel)

**Behavioural impact:** Pure additive. No existing routes, data, or UI
changed. Cron job is no-op on PASS so zero extra Mongo/CPU on healthy
systems.

### 2026-02-19 — Security Audit: datetime TypeError crash fix
*Session: continued*

**Problem:** `/api/admin/security/failed-logins` was crashing with
`TypeError: can't compare offset-naive and offset-aware datetimes` at
`routes/security_audit.py:90`. MongoDB returns naive `datetime` objects
but the comparison variable `now = datetime.now(timezone.utc)` is
timezone-aware, so `locked_until > now` raises whenever a lockout row
exists.

**Fix:** `backend/routes/security_audit.py` `failed_logins()`:
- Normalize naive `locked_until` values from Mongo to UTC-aware via
  `locked_until.replace(tzinfo=timezone.utc)` before the comparison.
- Guard `is_locked` with `isinstance(locked_until, datetime)` so
  non-datetime / None values can't raise either.
- Verified endpoint now returns 401 (auth required) rather than 500.

**Behavioural impact:** No more crash for admins loading the Security
Audit dashboard when any account is locked out.

### 2026-02-19 — Code-review follow-ups (two targeted fixes)
*Session: continued*

User ran automated code review; I audited findings and confirmed almost all
"critical" items were false positives (see notes inline in the session):
- `routes/auth.py:446` flagged as hardcoded secret — it's the documented
  `_CANONICAL_OWNER_PASSWORD` override, explicitly preserved per user.
- `backtester_service.py:193` flagged as `eval()` — it's a comment above
  the AST-based safe evaluator. No `eval()` call exists.
- `test_iteration36_code_quality.py:230/250` flagged — these tests verify
  the safe evaluator REJECTS dangerous input; removing them would remove
  security coverage.
- Test-file "hardcoded secrets" were dummy test fixtures.
- 212 React hook-dep warnings and localStorage findings are standard
  patterns, not bugs.

**Applied only the two legitimate low-risk items:**

1. `frontend/src/components/admin/ChipAdoptionInsights.jsx:144`
   - `key={i}` → `key={r.chip}` (matches the sibling Top Actions table
     already using `r.chip`). No behavioural change; fixes reconciliation.

2. Empty catch blocks now log at `console.debug` level (not `error` —
   these are all expected-silent paths, but DevTools can see them):
   - `hooks/useStreamingAgent.js` × 3 (SSE malformed-event parse)
   - `components/hubs/WarRoomHub.jsx` × 1 (user-cancelled Web Share)
   - `components/chat/ChatMessages.jsx` × 2 (fire-and-forget chip
     telemetry pings)

**Behavioural impact:** None. No API contracts, no UI, no auth changes.

### 2026-02-19 — Features grid uniform 3×2 layout
*Session: continued*

**Problem:** User spotted the "Built for Precision" feature grid on the
landing page looked misaligned — two cards (Nightly Dual-Signal Retraining
and GPT-5.2 Post-Mortem) used `md:col-span-2`, creating an asymmetric
"bento" layout (2+1 / 1+2 / 1+1) with an empty cell in the bottom-right row.

**Fix:** `frontend/src/components/LandingPage.jsx` Features component:
- Removed `span: 'md:col-span-2'` from cards 0 and 3.
- Removed `${f.span || ''}` from the card className.
- All 6 cards now render as a clean uniform 3-column grid × 2 rows; all
  cards equal width/height with matching text-wrap.

**Behavioural impact:** Visual polish only. No logic changed.

### 2026-02-19 — Reverted (not shipping): Pricing/Action-table width tweaks
*Session: continued*

After multiple iterations on the Pricing container width and the Action
pricing table layout (card-grid refactor, colgroup widths, etc.), user
requested the original state be restored. Reverted:
- Pricing section wrapper: back to `max-w-5xl` (original).
- Action pricing: back to the original `<table>` with 5 columns
  (Action / Free / Starter / Pro / Pro Max) and natural sizing.
- No net change on these two items for this session.

### 2026-02-19 — Landing-page pricing cards alignment fix
*Session: continued*

**Problem:** User flagged Pro card sitting visibly lower than Free/Starter/Pro Max
on the landing-page pricing grid (mobile + desktop). Root cause: Pro used
`border-2` (2px) while siblings used `border` (1px). That 1px delta made the
Pro card 2px taller + wider and pushed its content down relative to neighbors.

**Fix:** `PlanCard` in `frontend/src/components/LandingPage.jsx`:
- All cards now use `border-2` (equal 2px) with `border-transparent` + a
  `ring-1` for non-highlighted cards — keeps the subtle outline without
  the pixel shift.
- Added `flex flex-col h-full` so every card stretches to the tallest
  sibling (defensive — keeps rows aligned even if feature lists differ).
- Feature list marked `flex-grow` so CTA buttons line up at the bottom.

**Verified:** live preview screenshot (desktop + mobile) — all 4 cards
now have identical top + bottom edges, CTAs flush on the bottom row.


---

## 🟢 Shipped to production

> Blocks below were live at the time of the `mark-deployed` command. The
> commit hash is the state that was deployed — use `git diff <hash> HEAD`
> to see what's changed since.

### 2026-04-19 16:43 UTC — Shipped as `feb19-pricing-ml-retrain-resilience` (`1f76967`)

Commit: `1f769676be0e56b2dd6d12c037d9617a79269a42`

### 2026-02-19 — Dynamic NEUTRAL tolerance + live bot execution wiring
*Session: post-handoff recovery session*

**Behavioural changes (users may notice):**
- Accuracy stats on the dashboard now use **per-symbol ATR-based tolerance**
  instead of a flat 5%/2% band. Volatile tickers (NVDA, TSLA) get wider
  tolerance; low-vol ETFs (SPY) get tighter. Historical NEUTRAL calls were
  retro-rescored once under the new rule.
- Grid, Signal, and Webhook **trading bots in LIVE mode now actually execute
  through your connected broker** (Kraken for crypto, Alpaca for stocks).
  Previously only Paper mode worked; live had a TODO stub that silently did
  nothing. Owner-gated — no public users can trigger this.
- Price quotes cached with a **sliding 5-min TTL, max 3 touches** (~15 min
  max lifetime). Repeat quote reads for the same symbol never re-hit
  upstream until cache expires.
- Predictions deduped on a **sliding 15-min window, max 2 touches** (~30 min
  max lifetime). Rapid-fire repeat signals collapse onto the same
  `prediction_id` with `dedup_count` tracking. Cleaned 49 duplicate SPY
  NEUTRAL rows from the DB.
- `/api/accuracy/stats` and `/api/accuracy/history` now include a
  `pricing_freshness.disclaimer` explaining quote age — surfaced on the
  AccuracyBadge tooltip and as a small footnote in MemoryDashboard.
- **New owner-only** endpoints: `GET /api/admin/price-cache-stats` and
  `POST /api/admin/price-cache-invalidate/{symbol}` for cache ops
  visibility.
- **New public compliance page** at `/compliance/{broker}-oauth` (generic
  `/compliance/oauth` alias). Renders a full 3-legged OAuth 2.0 capability
  matrix pulled live from `/api/broker/oauth/capabilities`. Pre-built for
  Schwab, IBKR, Alpaca — adding more brokers to the backend config auto-
  populates new pages. Link added to landing page footer and logged-in
  app footer.

**Files touched (14):**
- `backend/services/sliding_cache.py` ★ NEW
- `backend/services/price_provider.py` — wired sliding cache into all 5 entrypoints
- `backend/services/prediction_tracker.py` — dynamic tolerance, dedup, disclaimer, `MAX_DEDUP_HITS=1`
- `backend/services/trading_bot_service.py` — live bot execution → broker
- `backend/routes/accuracy.py` — new `/rescore-neutral`, disclaimer wiring
- `backend/routes/admin.py` — owner-only cache stats endpoints + `_require_owner`
- `frontend/src/components/ComplianceOAuth.jsx` ★ NEW (parameterized compliance page)
- `frontend/src/App.js` — `/compliance/(broker-)?oauth` route
- `frontend/src/components/Footer.jsx` — "Compliance & Security" link
- `frontend/src/components/LandingPage.jsx` — footer "Compliance" link (only change)
- `frontend/src/components/AccuracyBadge.jsx` — disclaimer in tooltip
- `frontend/src/components/MemoryDashboard.jsx` — 9px slate-600 disclaimer footnote
- `memory/PRD.md` — changelog entries
- `memory/DEPLOYMENT_NOTES.md` ★ NEW (this file)

**Env vars added/changed:** none.

### 2026-02-19 — OpenAI direct API key rotation
*Session: continued*

**Env vars changed:**
- `OPENAI_API_KEY` rotated in `backend/.env`. Backend restarted. No code changes —
  existing OpenAI client code paths (still routed through Emergent LLM Key for
  most flows) pick up the new key automatically where direct OpenAI is used.
- Reminder: user should rotate this key at https://platform.openai.com/api-keys
  before production since it was transmitted in a chat session.

**Files touched:** `backend/.env` only.

### 2026-02-19 — Subscription price sync ($45 → $55)
*Session: continued*

**Problem:** User flagged the daily digest email still offered the Pro
plan at `$45/mo` while the frontend had been showing `$55/mo` for weeks.
Stale price drift across touchpoints.

**Files touched:**
- `backend/services/digest_service.py` — CTA in upgrade block (line 432)
- `backend/services/payment_service.py` — `SUBSCRIPTION_PRICE_MONTHLY`
  and `SUBSCRIPTION_PRICE_ANNUAL` constants ($45→$55 / $486→$594)
- `backend/tests/test_daily_digest.py` — assertion updated

**Verified clean:** full grep sweep of `/app/backend` and `/app/frontend/src`
returns no remaining `$45`/`45/mo`/`45.00 monthly` references. (The
`$45` in `test_iteration121_credit_system.py` is the "Power" credit
top-up SKU — legitimately different product, stays put.)

**Manual verification:** sent a fresh digest via `POST /api/digest/send-now`
to confirm new email output carries `$55/mo`.



**Behavioural changes:**
- **Nightly warm-up at 03:30 UTC** pre-resolves the top-500 federal
  recipients to tickers. Runs before the 06:00 daily digest so first
  gov-contracts dashboard load each morning is instant.
- **New owner-only endpoints:**
  - `POST /api/admin/usaspending-warmup?limit=N` — trigger on demand
  - `GET /api/admin/usaspending-health` — cache + config snapshot
- Warm-up is idempotent (rerun safely; already-cached entries skip OpenFIGI).
- **Telemetry insight:** top-100 federal recipients resolve 53/53
  publicly-traded names via hand map alone. 0 OpenFIGI calls needed at
  current scope. OpenFIGI stays as long-tail safety net.

**Files touched:**
- `backend/services/usaspending_service.py` — added `warmup_top_recipients()`
- `backend/routes/admin.py` — two new endpoints
- `backend/server.py` — added `usaspending_warmup` scheduled job


*Session: continued*

**Behavioural changes:**
- USASpending now uses a **two-stage resolver**: hand-curated 50-ticker
  map first (0ms, ~80% of the biggest awards), then OpenFIGI `/v3/search`
  fallback for long-tail names (ABBV, TER, MMM, biotech, etc.). Results
  cached in the existing `cusip_ticker_map` Mongo collection keyed by
  `query`.
- Shares the existing `OPENFIGI_API_KEY` — zero new env vars.
- Only negative-caches **confirmed** "no match" responses. Transient
  failures (429, 5xx, network) are not cached, so a temporary outage
  doesn't poison the lookup table.
- Ticker coverage for USASpending recipients effectively unlimited
  (OpenFIGI indexes ~10 million tradable securities).

**Files touched:**
- `backend/services/cusip_mapper.py` — added `resolve_name_to_ticker()`
- `backend/services/usaspending_service.py` — two-stage `_resolve_ticker()`
- `backend/route_registry.py` — wire `set_usaspending_db`

**Env vars:** unchanged (`OPENFIGI_API_KEY` reused).

### 2026-02-19 — USASpending.gov gov_contracts fallback
*Session: continued*

**Behavioural changes:**
- The only remaining gap after the Quiver resilience layer was
  `gov_contracts` (Quiver's endpoint returns 500 consistently, and we
  had no fallback). Added free public USASpending.gov as the fallback —
  `gov_contracts_count` went from 0 → 15 real awards (e.g. Boeing $32B
  DoD, Lockheed $30B DoD, Humana $51B DoD).
- Free API, no auth, no rate-limit concerns at our traffic.
- Hand-curated 50-ticker prime-contractor map (LMT, BA, NOC, RTX, etc.)
  filters USASpending recipients to only publicly-tradable names.
- 6-hour sliding cache matching the Quiver layer.
- Source string now reads `quiverquant+finnhub+scraping+usaspending`
  when all chains fire.

**Files touched:**
- `backend/services/usaspending_service.py` ★ NEW
- `backend/services/gov_filings_service.py` — USASpending fallback wired
  after Quiver returns empty

**Env vars:** unchanged (public API).


*Session: continued*

**Behavioural changes:**
- QuiverQuant's API has been returning 500s on 3 of 4 endpoints (lobbying,
  insiders, gov contracts) for weeks; only `congresstrading` works.
  Previously each request wasted a 30-second timeout — gov-filings page
  load was ~90s when 3 Quiver endpoints failed serially.
- New **per-endpoint circuit breaker**: 3 consecutive 5xx responses →
  endpoint skipped for 15 min, returns `[]` in ~0ms. Existing fallback
  chain (Finnhub → SEC scrapers in `gov_filings_service`) kicks in
  immediately instead of after a timeout cascade.
- New **6-hour sliding-TTL response cache**. Quiver "live" data updates
  once per business day, so repeat polls share the same response.
  `congresstrading` latency: 11s cold → 0ms hot.
- **Fixed path typo:** `govcontracts` → `govcontractsall` (matches the
  official `quiverquant` SDK). Our prior URL was 404ing before it even
  hit the 500s.
- **New owner-only endpoint** `GET /api/admin/quiver-status`: returns
  per-endpoint circuit state (`healthy` / `warning` / `open`), consecutive
  failure count, and cooldown seconds remaining. Cache stats included.

**Files touched:**
- `backend/services/quiver_service.py` — full rewrite with circuit +
  cache + correct paths
- `backend/routes/admin.py` — new `/quiver-status` endpoint

**Env vars:** unchanged.

**Known follow-ups:**
- Pattern 1 QuantConnect bridge (from user's discussion) deferred —
  user chose to fix the existing direct-API path first. If Quiver's
  backend outage persists > 1 month, revisit.

**DB changes:**
- Dropped 49 duplicate predictions (SPY @ $679.46, NEUTRAL, from prior session's runaway logger)
- Predictions now carry `last_seen_at`, `dedup_count`, and
  `verified_{24h,1w}.neutral_tolerance_used` fields — backward-compatible
  (absent on older rows)

**Auth / secrets:** unchanged. Owner creds remain `admin@risedual.ai` / `RiseDual2026!`.

**Known follow-ups (not blockers):**
- Schwab endpoints will go live the moment `SCHWAB_OAUTH_CLIENT_ID` /
  `SCHWAB_OAUTH_CLIENT_SECRET` are added to `backend/.env`.
