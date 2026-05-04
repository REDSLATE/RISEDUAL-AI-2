# RISEDUAL AI — Product Requirements Document

## 🛡️ Fork-Agent Guardrails — Code Review Triage Protocol

**Before applying any code review finding, verify it against the codebase
yourself.** External static analyzers (Snyk, SonarQube, DeepSource, etc.) produce
a significant fraction of false positives that look authoritative in a PDF
but damage working code if blindly applied.

**Required triage checklist:**

1. **Verify the claim** — open the flagged file and confirm the issue is
   real. Don't trust the line number + severity alone.
2. **Run the linter yourself** on the affected file (`ruff check <path>`,
   `npx eslint <path>`) — don't trust the count in the review.
3. **Check for substring-match false positives** on security findings.
   Common traps: `_pt_exec(...)` flagged as `exec()` because of the
   substring; `ast.parse(mode='eval')` flagged as `eval()`; `random` in
   non-cryptographic code paths flagged as insecure.
4. **Push back on quantity-over-quality** claims ("2,173 instances") —
   they almost always contain mostly-correct code swept up by an
   overzealous pattern. Sample 5 instances before agreeing to any bulk
   find-and-replace.
5. **Big refactors are deferred by default** — complexity reduction on
   working production code needs explicit operator sign-off + behavioural
   tests pinning the current contract before you change anything.

**Canonical reference**: `/app/memory/CODE_REVIEW_TRIAGE.md` documents
7 specific false positives already triaged on this codebase. If a future
review re-surfaces any of those 7 items, consult the triage doc before
touching code.

---

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

### Day-Trade Scanner Loop — FULLY WIRED (May 4, 2026)

Closes the P0 wiring gap from the previous session. Scanner service
+ exit monitor were created + unit-tested on 2026-05-03 but never
reached the scheduler or an admin surface. Now fully operational.

* **APScheduler wiring** in ``server.py``:
  * ``day_trade_scanner_equity`` — every 5 min (equity lane)
  * ``day_trade_scanner_crypto`` — every 5 min (crypto lane)
  * ``day_trade_exit_monitor`` — every 5 min (EOD 21:00 UTC close)
  * All three gated by ``DAY_TRADE_SCANNER_ENABLED`` (default ON).
  * Strict discipline: scan ALL symbols → rank → apply gates →
    queue exactly ONE winner per lane. Never execute while scanning.
* **Admin endpoints** in ``routes/admin.py`` (owner-only):
  * ``POST /api/admin/day-trade/scan/{equity|crypto}`` — manual trigger
  * ``GET /api/admin/day-trade/scan/recent?limit=20`` — scan log rows
  * ``GET /api/admin/day-trade/targets?status=pending&limit=50`` — queue
  * ``POST /api/admin/day-trade/exit-monitor/tick`` — manual EOD close
* **Live verified** (2026-05-04): equity + crypto scans both return
  ``total_scanned=0 candidates=[] chosen=null`` on first tick (cold
  start; predictions.timestamp window empty). Scan log persists,
  targets queue persists, exit monitor returns
  ``equity_closed=0 crypto_closed=0 errors=0``. 401 on unauth.
* **Pre-existing test debt cleared**: ``tests/test_crypto_paper_bot.py``
  ``_FakeDB`` fixture now auto-vivifies missing collections as AsyncMock
  (caught the ``crypto_adversarial_decision_log`` attribute error that
  was polluting the fixture), and the file pins
  ``CONFIDENCE_GATE_BASE=0.55`` at import-time so the bot-opening tests
  (which predate the 0.70 production raise) run against their
  historical regime. **26/26 green** (was 20/26). **47/47 green** in
  scanner + crypto-bot suites combined.



### Ticker Abandonment — Δ Since Yesterday (May 4, 2026)

The table now answers the operator's real question: "is this
ticker degrading or recovering?" — not just "what does the gate
say right now?".

* **New service** ``services/ticker_abandonment_history.py``:
  * ``compute_action_delta(today, yesterday)`` — pure function
    mapping action pairs to ``{delta, arrow, prior_action}``
    where delta ∈ ``new`` / ``improved`` / ``unchanged`` /
    ``degraded``. Action ranking pinned: ``ABANDON=0 <
    COOLDOWN=1 < KEEP=2``.
  * ``write_snapshot`` / ``fetch_prior_action`` — Mongo I/O,
    keyed by ``(date, lane, symbol)``. ``fetch_prior_action``
    is loose: returns the most recent snapshot strictly before
    today, even if the daily job missed yesterday.
  * ``snapshot_today(db)`` — walks the same union of symbols
    the bulk endpoint discovers, writes one snapshot per
    (lane, symbol). Idempotent same-day re-run.
* **Bulk endpoint update** — ``GET /api/admin/ticker-abandonment``
  now stamps ``delta`` onto every row AND opportunistically
  upserts today's snapshot. The operator never has to remember
  to run a script; just opening the page keeps history flowing.
* **Cron-friendly script** ``scripts/snapshot_ticker_abandonment.py``
  — guaranteed once-a-day trigger when no admin opens the
  page. Same idempotent contract.
* **Frontend column** in
  ``TickerAbandonmentTable.jsx`` — single-char arrow column
  between Action and Reason, color-coded (improved=emerald,
  degraded=rose, unchanged=slate, new=cyan), with a tooltip
  showing yesterday's action.
* **17 new tests** in
  ``tests/test_ticker_abandonment_history.py`` pinning all 4
  delta transitions, action-rank invariant, idempotency,
  loose-fallback behaviour, and the snapshot-today integration
  walk.
* **Live verified end-to-end**: injected a synthetic
  "yesterday" snapshot for AAPL (COOLDOWN) and BTC (KEEP),
  hit the endpoint, observed AAPL = ``improved ↑`` (recovered)
  and BTC = ``unchanged →``. Other 9 symbols correctly
  reported ``new •``.



### Ticker Abandonment Bulk Overview + Admin Table (May 4, 2026)

* **New endpoint** ``GET /api/admin/ticker-abandonment`` —
  bulk-discovery across ``paper_trades`` (equity), ``crypto_paper_trades``
  (crypto), and ``agent_activity`` ``paper_trade_*`` events
  (catches symbols that ONLY get rejections). Computes the
  gate decision per (lane, symbol) and sorts ABANDON →
  COOLDOWN-by-descending-cooldown → KEEP. Returns row list +
  totals.
* **New component** ``frontend/src/components/admin/TickerAbandonmentTable.jsx``
  — polls every 60s, renders one color-coded row per (lane,
  symbol) with action pill, reason, signals/rejections/wins/
  losses, avg confidence, avg RR, cooldown duration. Same visual
  language as the AdversarialCoresChip; lives under it in the
  Terminal admin tab.
* **Live verified**: 11 tickers discovered (1 equity AAPL + 10
  crypto majors), all currently KEEP because no symbol has
  accumulated 5+ recent signals yet — exactly the expected
  cold-start behaviour.



### Ticker Abandonment / Cooldown Gate (May 4, 2026)

> "A ticker can be watched forever, but it cannot consume trading
> bandwidth forever."

The bots can now LET GO. New gate runs FIRST in both paper-trade
loops — bad ticker behaviour reduces attention before it consumes
capital.

* **New module** ``services/ticker_abandonment.py`` — pure
  ``decide_ticker_exit`` returning ``KEEP / COOLDOWN / ABANDON``
  across 6 spec'd branches: insufficient data, toxic
  loss-cluster, high rejection rate, poor avg RR, low-confidence
  churn, stale-no-recent-profit. Stateless: rolling-window
  inputs heal naturally once the ticker stops being evaluated.
* **New stats helper** ``services/ticker_abandonment_stats.py``
  — ``compute_equity_inputs`` and ``compute_crypto_inputs``
  aggregate the 7 inputs from existing collections
  (``agent_activity`` for signals + rejections,
  ``paper_trades`` / ``crypto_paper_trades`` for outcomes +
  RR + last-profitable). 30-day window, env-tunable via
  ``TICKER_ABANDONMENT_WINDOW_DAYS``.
* **Integration** as the FIRST gate in both
  ``services/ml_paper_trader.run_paper_trade`` and
  ``services/crypto_paper_trader.run_crypto_symbol``. COOLDOWN
  / ABANDON skips the trade and emits a
  ``paper_trade_skipped`` activity event with the gate's
  reason (e.g. ``ticker_cooldown_high_rejection_rate``).
  Defensive: any gate failure logs + falls through (gate can
  never block a healthy trade loop).
* **Admin endpoint**
  ``GET /api/admin/ticker-abandonment/{symbol}?lane=equity|crypto``
  — owner-gated, inform-only. Returns the resolved inputs and
  the gate's current decision. Live verified end-to-end on
  both lanes.
* **22 new tests** in ``tests/test_ticker_abandonment.py`` —
  one per spec branch, branch-precedence ordering (ABANDON
  beats high-rejection cooldown), KEEP-path full coverage,
  frozen-dataclass invariant.



### Reasoning Overlay — Plumbed to Paper Traders (May 4, 2026)

The reasoning overlay now reaches the rich gate-trace context from
both paper traders, surfacing the COMMANDER_DISAGREES /
PHASE2_BRAKE_VETO / INTEGRITY_MITIGATION_ACTIVE codes the user
wanted on every new trade row.

* **Two new pure adapters** in
  ``services/decision_reasoning_overlay.py``:
  * ``build_equity_paper_trade_decision_view(trade_doc, *,
    snapshot, patterns, dynamic_conf_threshold)`` — walks the
    equity ``paper_trades`` row + brake/sovereign/failure blocks
    and emits ``passed_gates`` (confidence, dynamic-threshold,
    pattern, regime, phase2-brake-pass) /
    ``failed_gates`` (phase2-brake-veto) /
    ``risk_adjustments`` (sovereign delta, integrity mitigation,
    brake throttle) /
    ``commander_shadow`` (with authority=ACTIVE only when
    ``promotion_phase == "full"``).
  * ``build_crypto_paper_trade_decision_view(trade_doc, *,
    signal)`` — same shape for ``crypto_paper_trades``, sourcing
    Commander vote from the signal payload (the trade row stores
    only ``adversarial_decision_id``, not the Commander vote).
* **Bug caught + fixed during smoke**: equity ML paper trader
  persists ``direction="up"/"down"`` (the ML signal enum), not
  ``LONG/SHORT``. Without normalisation every equity trade would
  have landed in ``NEUTRAL_ACTION``. Added
  ``_normalise_equity_direction`` inside the adapter (overlay
  itself stays purist; the canonicalisation happens at the
  adapter boundary).
* **Integration** in both ``ml_paper_trader.py`` (right before
  ``paper_trades.insert_one``) and ``crypto_paper_trader.py``
  (right before ``crypto_paper_trades.insert_one``). Same
  ``try/except → debug log + skip`` failure mode as the
  ``log_prediction`` integration — overlay can never block a
  trade insert.
* **12 new tests** in
  ``tests/test_reasoning_overlay_adapters.py`` pinning: input
  immutability, up/down normalisation, COMMANDER_DISAGREES,
  brake-veto → FAILED gate, full-phase → ACTIVE authority,
  sovereign threshold filtering, ``adversarial_action`` →
  Commander authority for crypto, COMMANDER_AGREES path.
* **Live smoke**: equity scenario with brake-pass + Commander
  disagree + symbol failure penalty produced
  ``[REGIME_SUPPORTS_LONG, PASSED_ALL_GATES,
  INTEGRITY_MITIGATION_ACTIVE, COMMANDER_DISAGREES,
  COMMANDER_NO_AUTHORITY]`` and summary "Strategist favored a
  long position under TRENDING_BULL regime while Commander
  suggested SHORT (shadow only)."



### Reasoning Overlay Backfill (May 4, 2026)

* **New script** ``scripts/backfill_reasoning_overlay.py`` —
  one-off, idempotent, with ``--dry-run`` preview mode. Walks every
  prediction lacking the ``reasoning`` field and stamps the
  calibration-aware overlay onto it. Read-only by construction —
  the overlay is a pure function and ``direction`` / ``confidence``
  are never touched.
* **Live applied**: 609 of 665 historical prediction rows
  backfilled. Remaining 56 are pre-schema rows that legitimately
  lack ``prediction_id`` and were correctly skipped.
* **Reason-code distribution after backfill**:
  ``PASSED_ALL_GATES`` 609,
  ``REGIME_SUPPORTS_LONG`` 377,
  ``NEUTRAL_ACTION`` 162,
  ``REGIME_SUPPORTS_SHORT`` 70.
* **Idempotency verified** — second invocation reports
  ``updated: 0 rows``.
* **Operator query now possible**:
  ``db.predictions.find({"reasoning.reason_codes":
  "UNDERCONFIDENT_MODEL"})`` (will start returning rows as new
  predictions land carrying the calibrated_confidence field).



### Decision Reasoning Overlay (READ-ONLY) (May 4, 2026)

Every prediction now carries a `reasoning` field with human +
machine-readable explanation alongside the existing schema. Pure
function, never mutates input — same audit-stable discipline as the
calibration scope.

* **New module** `services/decision_reasoning_overlay.py` —
  `build_reasoning_overlay(decision)` returns
  `{summary, bull_case, bear_case, what_would_change_decision,
  reason_codes, meta}`. Reason codes include
  `REGIME_SUPPORTS_LONG/SHORT`, `NEUTRAL_ACTION`,
  `UNDERCONFIDENT_MODEL` / `OVERCONFIDENT_MODEL` (powered by the
  calibration delta), `PASSED_ALL_GATES` /
  `FAILED_ONE_OR_MORE_GATES`, `SMALL_SAMPLE_DISCOUNT`,
  `INTEGRITY_MITIGATION_ACTIVE`, `COMMANDER_AGREES`/`DISAGREES`,
  `COMMANDER_NO_AUTHORITY`.
* **Integration** in `services/prediction_tracker.log_prediction`
  — overlay stamped onto every prediction doc just before insert,
  via a synthetic `decision_view` dict (preserves on-disk schema:
  the doc still uses `direction` not `action`; `reasoning` is the
  only new field). Defensive against missing data — falls back to
  baseline reasoning when upstream call sites don't carry
  `passed_gates` / `commander_shadow`.
* **Read-only contract pinned by tests**:
  `tests/test_reasoning_overlay.py` — 20 cases including the
  user-spec smoke test, an explicit
  `test_overlay_does_not_mutate_input` invariant (deep-copy
  comparison), all reason-code dispatches, calibration
  awareness, commander logic, defensive empty-dict handling.
* **Live verified** end-to-end: a smoke prediction at raw 0.56
  / calibrated 0.917 produced reason codes
  `[REGIME_SUPPORTS_LONG, UNDERCONFIDENT_MODEL, PASSED_ALL_GATES]`
  + bear case "model confidence may be understated", exactly as
  spec'd.



### Raw vs Calibrated Tier 3 Badge (May 4, 2026)

Operator visibility for the calibration work — both the email digest
and the admin dashboard now surface "calibrated **X.X** · raw **Y.Y**"
side-by-side whenever an active calibration model is present, so the
calibration's impact is visible at a glance instead of buried in
``test_calibration_service.py``.

* ``services/tier3_readiness.build_tier3_stats`` gained a
  ``use_calibrated`` kwarg (default True). Setting it False forces
  the legacy raw-only aggregation path.
* ``tier3_readiness_snapshot`` now runs both passes when calibration
  is active, returning ``raw_view`` + ``calibration`` blocks
  alongside the existing top-level shape (no breaking changes for
  any existing consumer; sizing/execution untouched).
* ``services/research_shadow_stats.fetch_tier_readiness`` exposes
  ``tier3_progress_pct_raw`` + ``tier3_calibration`` for the admin
  dashboard card.
* ``services/tier3_readiness_digest._format_body_html`` renders the
  badge as a single inline pill: "calibrated 88.0 · raw 70.5 · ECE
  35.6pp → 1.06pp (scope: tier3 readiness only)".
* ``frontend/src/components/admin/Tier3ProgressDetailCard.jsx``
  renders a cyan inline badge next to the composite score, with a
  full-context tooltip pinning the
  ``applies_to=["tier3_readiness_only"]`` boundary.
* ``tests/test_tier3_readiness_calibration_badge.py`` — 3 cases
  pinning: snapshot omits ``raw_view`` without calibration; both
  views computed when active; top-level still uses calibrated path
  (sizing reader contract).



### Confidence Calibration (Tier 3 readiness only) (May 4, 2026)

Audit of the 147-row verified-prediction corpus revealed structural
under-confidence: bulk of the volume sat at raw 0.5-0.7 confidence
with 88-97% actual win rates. **Weighted ECE: 35.56pp.** First fit
brought it to **1.06pp** — a 33× improvement.

Critical guardrails (operator-pinned 2026-05-04):

* **Raw ``confidence`` is never overwritten** — calibrated value
  lands in a separate ``calibrated_confidence`` field.
* Sizing (``ai_core/sizing.compute_*``), execution gate
  (``MIN_CONFIDENCE_TO_TRADE``), and signal-bot quantity scaling all
  keep reading raw ``confidence``. Calibration is consumed ONLY by
  ``services.tier3_readiness``.
* Per-row audit field ``calibration_applies_to=["tier3_readiness_only"]``
  makes the boundary explicit at the data layer — anyone who later
  wants to use this signal for sizing has to change that list,
  triggering code review.
* No auto-schedule yet — one-off fit script
  (``scripts/fit_calibration_from_history.py``) for now; weekly
  refresh deferred until first month of evidence is reviewed.

New components:

* ``services/calibration_service.py`` — isotonic fit
  (``LOOKBACK_DAYS=90``, ``MIN_CALIBRATION_ROWS=100``,
  ``MAX_CALIBRATED_CONFIDENCE=0.95``), pure ``apply_calibration``,
  ``calibration_audit_fields`` (full per-row envelope for the
  writer), ``get_active_calibration``.
* ``services/prediction_tracker.log_prediction`` write-path hook —
  stamps ``calibrated_confidence`` + audit fields before insert when
  an active model exists. No-op fallback when no model.
* ``services/tier3_readiness._high_conf_and_grades`` read-path
  change — prefers ``calibrated_confidence`` per row, falls back to
  raw for legacy rows.
* ``GET /api/admin/calibration/status`` (owner-gated) — version,
  fit timestamp, ECE before/after, knot table, ``applies_to``
  scope.
* ``scripts/fit_calibration_from_history.py`` — one-off + ``--print``
  preview mode.
* ``tests/test_calibration_service.py`` — 13 cases pinning purity,
  monotonicity, max-cap, 0-1/0-100 scale handling, refusal below
  ``MIN_CALIBRATION_ROWS``, ECE-improvement invariant, persisted
  ``applies_to`` boundary.

First fit (live preview):

::

    version       : isotonic_2026_05_04T09_13_25
    n_rows        : 147
    ece_before_pp : 35.56
    ece_after_pp  : 1.06
    knots         : x=0.00→y=0.667, x=0.16→y=0.667,
                    x=0.47→y=0.917, x=1.00→y=0.917



### Adversarial Promotion Gate + Warm-Up Indicator (May 4, 2026)

Operator now has at-a-glance visibility into when the adversarial cores
have earned the right to advance through ``shadow → risk_only → veto →
full``.

* **New service** ``services/adversarial_promotion_gate.py`` —
  ``compute_promotion_status(db)`` aggregates the full
  ``crypto_adversarial_decision_log`` and returns:
  * ``commander_correct_rate`` (decision=LONG+winner=bull OR
    decision=SHORT_OR_AVOID+winner=bear, divided by total closed rows)
  * ``trustworthy`` flag once ``closed_lifetime ≥ 20`` (chip warm-up)
  * ``ready_to_promote`` against per-transition row+rate floors
    (defaults: 20/55%, 50/58%, 100/60% — all env-tunable)
  * Copy-pastable ``promote_env_line`` like
    ``CRYPTO_ADVERSARIAL_PHASE=risk_only``
  * Terminal phase ``full`` returns ``next_transition=None``.
* **New endpoint** ``GET /api/admin/adversarial-cores/promotion``
  (owner-gated, mirrors the existing ``/24h`` shape).
* **AdversarialCoresChip.jsx** now polls both endpoints every 30s and
  surfaces:
  * Promotion pill in the header — ``Ready → risk_only`` (emerald),
    ``N rows to risk_only`` (slate), or ``TERMINAL · full`` (violet).
  * Warm-up banner under the header until the trustworthy floor is
    crossed (replaces the misleading "0% spread" early state).
  * Bull−Bear spread now uses the lifetime aggregation (more stable
    than the 24h slice it was reading before) and only renders once
    trustworthy.
  * Copy-button drawer with the env line + restart command when the
    gate flips green. Inform-only — never flips the env on its own
    (audit-stable, matches ``commander_phase2_brake`` discipline).
* **Tests**: ``test_adversarial_promotion_gate.py`` — 10 cases
  pinning empty/cold-start envelope, trustworthy threshold,
  shadow→risk_only / risk_only→veto / terminal-full transitions,
  NO_TRADE exclusion from commander-correct, lifetime-spread math,
  env override.


### Data-Integrity Gate AST lint — fully green (May 4, 2026)

Closed out the remaining 17 ``test_no_local_direction_tuples.py``
violations exposed when the gate first ran end-to-end.

* **Refactored to ``canonical_ai_dir``** (preferred path):
  * ``services/sovereign_ai_core.py`` (3 sites — adversarial
    amplification + crypto strategist mapping)
  * ``services/sovereign_resolution_loop.py`` (``_was_right`` direction
    folding)
  * ``services/conviction_service.py`` (catalyst alignment check)
* **Allowlisted with justification** (4 files): ``commander_phase2_brake``
  + ``smart_money_verification`` (engine-specific synonym folding to
  canonical inside pure functions), ``natural_language_trading``
  (BUY/SELL stopword filter for ticker extraction), ``terminal_aggregator``
  (post-canonicalisation invariant checks).
* **Verified**: ``pytest tests/test_no_local_direction_tuples.py``
  green; full direction-grading + sovereign + conviction + terminal
  suite (179 tests) passes.



### Adversarial Monitor + SSE Stream + Phase 2 EXIT (May 4, 2026)

Three tightly-coupled follow-ups shipped together — operator
monitoring surface, real-time push, and price-driven EXIT triggers.

**(A) Adversarial Cores monitor**
* New module ``services/adversarial_monitor.py::summarize_24h`` —
  24h rollup of ``crypto_adversarial_decision_log``: decision counts
  (LONG/SHORT_OR_AVOID/NO_TRADE), per-agent wins, win rates (``None``
  until closed rows exist so no false-precision on cold-start), avg
  edge_gap, avg Bull/Bear confidences, last decision timestamp.
* New endpoint ``GET /api/admin/adversarial-cores/24h`` (owner-gated).
* New frontend chip ``components/admin/AdversarialCoresChip.jsx``
  rendered inside the admin Terminal tab. Traffic-light status:
  ``OFF`` (env flag unset, slate), ``IDLE`` (enabled but no rows yet,
  amber), ``LEARNING`` (enabled + rows flowing, emerald). Shows
  decisions count, closed count, avg edge_gap, last-run age, per-side
  confidence + win stats, and a Bull−Bear win-rate spread in
  percentage-points.
* Tests: ``test_adversarial_monitor.py`` — 6 cases.

**(B) SSE stream for top-actions**
* New endpoint ``GET /api/terminal/top-actions/stream`` using
  ``sse_starlette.EventSourceResponse``. Pushes ``event: snapshot``
  whenever a structural hash of the actions list changes (new
  sovereign decision, paper-trade open/close, SL/TP breach flip);
  heartbeat every 30s otherwise. Cookie-auth compatible so vanilla
  ``new EventSource(url)`` on the frontend just works.
* Frontend ``TerminalTopActions.jsx`` now opens an SSE subscription
  alongside a 60s polling fallback — stream-status pill shows
  ``● live`` (emerald) when connected or ``○ poll`` (slate) when
  degraded. Auto-reconnects are delegated to the browser's
  EventSource implementation.

**(C) Phase 2 EXIT — hard SL/TP breach detection**
* New pure-function classifier
  ``terminal_aggregator::_hard_exit_trigger(direction, price, sl, tp)``
  — LONG SL breach: price ≤ SL; LONG TP hit: price ≥ TP; SHORT rules
  symmetrical; zero-price short-circuits (never fabricate an EXIT on
  a stale price).
* New async helper ``_build_hard_exit_cards(ctx, open_positions,
  price_fetcher=...)`` — probes each open position, fires a
  hard-EXIT card per breach. Priority floor 0.99 (above the 0.95
  reversal-EXIT floor, so hard breaches always rank first). Conviction
  tier ``"hard"``. Reason text includes trigger type + live price +
  entry/SL/TP for operator context.
* Wired into ``get_top_actions``: hard-EXIT cards are injected before
  the sovereign loop, AND the breached symbol is stripped from the
  reversal-EXIT / orphan-MANAGE paths so we never render two EXITs
  for the same asset on one tick.
* Broken price fetcher degrades silently (returns ``[]``) — a dead
  live-price provider can never suppress the rest of the response.
* Equity SL/TP merged from ``portfolios.positions[].stop_loss/take_profit``
  (inline on crypto_paper_trades); currently `portfolios` is empty in
  the test DB so only crypto hard-EXITs fire today.
* Tests: ``test_terminal_hard_exit.py`` — 14 cases (pure classifier
  parametrized × 10, card builder normal/broken/empty × 3,
  integration tests proving hard-EXIT outranks reversal + suppresses
  duplicate MANAGE + rank=1).

**Totals**: **105/105 green** across adversarial + terminal +
nl_live_bridge + kraken_shadow + terminal_aggregator suites. Lint
clean. Live-verified: `/api/admin/adversarial-cores/24h` returns
enabled=True/phase=shadow, `/api/terminal/top-actions/stream` returns
HTTP 200 with `content-type: text/event-stream` and first frame
`event: snapshot`. Testing agent iteration_165: **100% backend (9/9)
+ 100% frontend**, zero issues, zero action items. Live data showing
4 ENTER + 1 WATCH cards from the active sovereign core.

### Adversarial Core — unblocked for shadow recording & learning (May 4, 2026)

**Operator directive**: *"There should be nothing blocking them.
Record the moves and learn from the decisions it makes."*

**Diagnosis** (before this change):
* `crypto_adversarial_decision_log`: **0 rows** (idle)
* `sovereign_decisions`: 241 rows, `prd_resolved_outcomes`: 269 rows
  — the **Sovereign** core was fine; the **Bull/Bear/Commander**
  layer was silent.
* Root cause: ``run_adversarial_decision()`` had a **double gate** —
  env flag ``CRYPTO_ADVERSARIAL_ENABLED`` (unset) AND Tier 3 unlock
  (locked with ``days: 2``, only 9 high-confidence samples). The
  Tier 3 gate created a chicken-and-egg problem: the layer couldn't
  run until Tier 3 unlocked, but Tier 3 couldn't unlock without the
  evidence the layer was supposed to generate.

**Fix** (`services/adversarial_core.py`):
* **Tier 3 gate now only blocks non-shadow phases** (``risk_only`` /
  ``veto`` / ``full``) — i.e., anything that can alter live trades.
* **Shadow phase runs on the env flag alone**, since shadow has no
  live mutation by design. The Bull/Bear/Commander decisions flow
  through to ``log_adversarial_decision`` unconditionally under the
  operator's kill-switch.
* Tier 3 remains the safety net for promotion to live-influencing
  phases. No change to how ``phase != shadow`` is guarded.

**Env flags** (flipped in ``/app/backend/.env``):
* ``CRYPTO_ADVERSARIAL_ENABLED=1`` — operator kill-switch ON.
* ``CRYPTO_ADVERSARIAL_PHASE=shadow`` — observation-only (default).

**Learning loop already wired**:
* Open: ``crypto_paper_trader.py:282`` logs the full decision
  payload to ``crypto_adversarial_decision_log``.
* Close: ``crypto_closer.py:259`` calls ``update_decision_outcome``
  with the realised R-multiple, patching winner (Bull/Bear/Commander)
  back onto the original decision row.
* Stats aggregator: ``crypto_adversarial_stats.py`` reads back the
  closed-outcome rows to compute per-agent win rate, edge-gap
  distribution, and per-regime calibration for the Adversarial
  admin panel.

**Live verified end-to-end**:
* Invoked ``run_adversarial_decision`` with a realistic BTC signal.
* Bull agent: conf 0.763 / Bear agent: conf 0.441 / Commander:
  ``NO_TRADE`` (edge_gap 0.30, just below 0.35 threshold).
* Logged with UUID ``9142f939-…`` into
  ``crypto_adversarial_decision_log`` (0 → 1 rows).
* Phase ``shadow`` — no live trade impact.

**Tests updated**: `test_adversarial_core.py` got two new policy
assertions — ``test_double_gate_tier3_locked_still_records_shadow``
pins the new "shadow always runs" contract, and
``test_tier3_locked_blocks_non_shadow_phases`` pins Tier 3 still
guards any live-impacting phase. **22/22 green** in the adversarial
suite, **125/125 green** across adversarial + terminal +
nl_live_bridge + kraken_shadow + crypto_adversarial_phase_wiring +
crypto_adversarial_stats suites. Lint clean.

### Terminal Top Actions — Frontend tab + EXIT rule layer (May 3, 2026)

**Two follow-ups to T2** shipped in one pass — the visual layer for
`/api/terminal/top-actions` and the EXIT card rule logic.

**Frontend** — new `components/admin/TerminalTopActions.jsx` rendered
inside the Admin panel as a new "Terminal" tab in the Operations
group. Polls `/api/terminal/top-actions?limit=10` every 30s.
Renders:
* A 4-chip totals row (EXIT / ENTER / MANAGE / WATCH).
* A vertical stack of action cards, each with rank, symbol, side
  arrow, conviction score, priority score (0-100), reason text,
  veto count, shadow flag, and an optional correlation_note chip.
* Per-kind colour coding — EXIT in rose, ENTER in emerald, MANAGE
  in slate, WATCH in amber. EXIT cards visually scream by design.
* Empty state when sovereign is quiet ("normal during market closes
  or low-conviction regimes").

**EXIT rule layer** — `_classify_kind()` now emits ``"EXIT"`` when:
* The user has an open paper_trade on the symbol AND
* The latest sovereign decision **reverses** the side (LONG vs
  SHORT) AND
* Conviction is high (``tier ∈ {high, STRONG, VERY_STRONG}`` or
  ``confidence ≥ 0.70``).

EXIT cards get a **priority-score floor of 0.95** so they always
rank above any new ENTER candidate within the same response — the
operator must see the reversal first. Reason text explicitly names
the reversal: ``"Sovereign reversed to SHORT on open LONG position
· high tier · conf 0.82"``. Aligned signals stay MANAGE_POSITION;
low-conviction reversals stay MANAGE_POSITION (noise filter).

**Hard SL/TP breach detection deferred** — the equity portfolio
storage collections are empty in the current test DB and crypto
SL/TP fields aren't populated on most rows; needs a live-price hop
to be meaningful. v1 ships sovereign-reversal as the only EXIT
trigger, which works on both equity and crypto with no new I/O.

**Tests**: 5 new EXIT-specific cases added to
``test_terminal_top_actions.py`` (LONG-position + high-conviction
SHORT → EXIT, SHORT-position + high-conviction LONG → EXIT,
low-conviction reversal stays MANAGE, aligned signal stays MANAGE,
EXIT outranks ENTER on the same tick). **16/16 green** in the
terminal_top_actions suite, **63/63 green** across all
terminal-related + sparkline + kraken_shadow + nl_live_bridge
suites. Lint clean.

**Testing agent verification (iteration_164)**: backend 8/8 API
tests passed, frontend 100% verified — Terminal tab renders, totals
chips visible, action cards display all schema fields, refresh
button works, existing /signal/{symbol} and /market-state
endpoints unaffected. Live data showed 1 ENTER (BTC) and 7 WATCH
cards from the 217 real sovereign_decisions rows. EXIT path
correctly stays empty in live data because no admin paper_trades
exist; unit tests cover the EXIT logic.

### Terminal Top Actions — T2 endpoint (May 3, 2026)

**P0 from the handoff backlog**: ``/api/terminal/top-actions`` ships
the operator's prioritized action queue for the Market State
Awareness Terminal UI. Composes ``sovereign_decisions`` (latest per
symbol over the last 24h) with the user's open ``paper_trades`` /
``crypto_paper_trades`` into ranked action cards.

**New endpoint**: ``GET /api/terminal/top-actions?user_id=&limit=10``
(owner-gated). Empty list on cold-start is a valid response.

**Action kinds**:
* ``MANAGE_POSITION`` — symbol has an open user paper-trade (never
  silently doubles-down even if a fresh ``LONG`` signal arrived).
* ``ENTER`` — high-conviction (``tier ∈ {high, STRONG, VERY_STRONG}``
  or ``confidence ≥ 0.70``) ``LONG/SHORT`` signal on a symbol with no
  open user position.
* ``WATCH`` — every other signal worth surfacing.
* ``EXIT`` — reserved for the close-side rule layer (not yet wired).

**Ranking score** (in ``[0, 1]``):
* 70% conviction (50% tier-anchor + 50% raw confidence).
* 20% Sovereign size_multiplier.
* 10% freshness decay (linear over the 24h lookback).

**Default correlation rules** (operator skipped explicit rules):
* Hard cap of **5 ENTER cards** per response. The 6th-and-beyond
  ENTER candidate is demoted to ``WATCH`` with a ``correlation_note``
  explaining the concentration cap. The operator still sees the
  signal, just without the "act now" prompt.
* No sector / beta correlation logic at T2 default settings (the
  symbol→sector resolver and beta data exist but are out of scope
  for the default path; re-open if tighter coupling is needed).
* ``MANAGE_POSITION`` cards always surface for open positions, even
  with no fresh sovereign view in the window — the operator never
  loses visibility on a position because the model went quiet.

**Per-card payload** (pinned by ``test_top_actions_action_payload_shape``):
``kind, symbol, asset_type, action, priority_score, conviction,
size_multiplier, vetoes, vetoes_count, reason, correlation_note,
decision_id, decision_age_minutes, shadow, links.signal, rank``.

**Tests**: 11 cases in ``test_terminal_top_actions.py`` covering
cold-start envelope, dedup-to-freshest-per-symbol, lookback exclusion,
high-conviction LONG → ENTER, open-position reroute to MANAGE_POSITION,
6-position concentration cap → WATCH+correlation_note, priority-score
ranking monotonicity, orphan-position MANAGE surface, ``limit``
clamp [1, 50], low-confidence HOLD surfaces as WATCH (not silenced),
and the per-card payload schema. **113/113 green** across terminal +
ingestion + kraken_shadow + nl_live_bridge + confidence_gate +
natural_language + integrity_mitigation suites. Lint clean. Live
endpoint verified — 401 unauth, owner-cookie returns ranked actions
composed from 217 real sovereign_decisions rows in the test DB.

### Kraken xStock Shadow — re-enabled per operator (May 3, 2026)

Operator confirmed: empty-row days are themselves signal ("slow days
are just as important as busy days"). ``KRAKEN_SHADOW_ENABLED=1`` is
back on permanently. The shadow lane runs every 5 min, persists
``not_listed`` sentinels for symbols Kraken's US-egress AssetPairs
won't expose (currently all top-20 ML symbols), and writes empty
ticks into the audit trail. When egress changes (proxy, EU pod) or
Kraken lifts the geo-restriction, the inflection point will be
visible in the data with no code change required.

Also corrected the Kraken P2 ROADMAP entry — Phase 1 is no longer
"paper/shadow equity routing" (Kraken doesn't offer paper trading);
paper stays on Alpaca. Phase 1 is now framed as live equity orders
behind the same geo gate that Phase 0 needs.

### Burn-in Ingestion Sparkline — Benzinga + AV (May 3, 2026)

P1 burn-in observability: a small two-line SVG sparkline on the
admin burn-in panel showing hourly Benzinga + Alpha Vantage article
ingestion counts over a rolling N-hour window (default 24h, cap 168h).
Lets ops spot a stalled feeder at a glance instead of digging through
catalyst_events row counts.

**New endpoint**: ``GET /api/admin/news-shock/ingestion-sparkline?hours=24``
(owner-gated). Returns ``{hours, buckets[], benzinga[], alpha_vantage[],
totals: {benzinga, alpha_vantage}, current_hour: {benzinga,
alpha_vantage}}``. Single Mongo aggregation against ``catalyst_events``
with ``$dateTrunc`` hour buckets — cheap, indexed by ``event_time``.
Hours param is bounded ``[1, 168]`` so a runaway query doesn't pull
the whole event log.

**New frontend component**: ``components/admin/IngestionSparkline.jsx``
— pure inline-SVG (no chart library, no bundle bloat), two stacked
sparklines coloured emerald (Benzinga) and blue (AV) with explicit
24h totals + current-hour counter beside each line. Renders an
"empty state" card when both sources are silent for the window so
ops can distinguish "feeder paused" from "render bug".

**Tests**: 3 cases in ``test_ingestion_sparkline.py`` covering: 401
unauth, hours=24 returns the seeded benzinga (6) + AV (2) counts in
the right buckets, and ``hours=0`` floors to 1 / ``hours=999`` caps
to 168. **89/89 green** across ingestion + kraken_shadow +
nl_live_bridge + confidence_gate + natural_language +
integrity_mitigation. Lint clean. Backend healthy.

### Kraken xStock Equity Shadow — Phase 0 (May 3, 2026)

> **⚠️ Geo-block discovered on activation (2026-05-03)**: probing
> Kraken's public REST live from this US-egress pod shows **zero
> tokenized_asset pairs** — Kraken regulatory-gates xStocks to
> non-US jurisdictions. ``KRAKEN_SHADOW_ENABLED`` was flipped on
> briefly for verification, returned ``no_listed_xstocks`` cleanly,
> and was rolled back. Infrastructure is intact and tested; needs
> non-US egress (proxy / EU pod) before it can produce data.
> Full re-activation runbook in `ROADMAP.md` under the Kraken P2
> entry.

Phase 0 of the Kraken US-equities ("xStocks") rollout: **public market
data only**, shadow-compared against our existing Alpaca / AV equity
quote provider. **No orders, no auth.** Default OFF — opt in via
``KRAKEN_SHADOW_ENABLED=1``.

**New module**: ``services/kraken_equity_shadow_service.py``
* ``run_kraken_shadow_compare_once(db)`` — one tick: discover pair
  metadata, batch-fetch tickers, compare each canonical symbol's mid
  vs Alpaca's mid, persist one row per (symbol × tick).
* Symbology resolution via ``/0/public/AssetPairs?asset_class=tokenized_asset``
  — fast-path on canonical ``<SYM>USD`` pair codes, falls back to
  ``altname`` and ``base/quote/aclass_base`` matching. Symbols with no
  Kraken xStock listing get a ``not_listed`` sentinel cached for the
  TTL window so we don't re-hammer AssetPairs each tick.
* Rate-limit-respecting batching: 100-symbol batches with 60s spacing
  inside the 5-min scheduler interval = ~5 batches per tick at <1
  request/sec, well under Kraken's public ceiling.
* ``market_session()`` classifier (regular / pre / post / closed via
  static ET offsets + 2026 US-holiday set). Slack alerts only fire in
  ``regular`` session — xStocks legitimately trade against staled
  Alpaca closes outside RTH so off-hours divergence is expected and
  non-actionable.
* Slack alert path mirrors ``integrity_mitigation_service`` — uses
  ``SLACK_WEBHOOK_URL`` env var, never raises, single webhook post per
  threshold breach.

**New collections**:
* ``xstock_pair_metadata`` — ``{canonical_symbol, kraken_pair, altname,
  status, last_seen}``. Unique index on ``canonical_symbol``.
* ``kraken_equity_shadow_compare`` — ``{symbol, kraken_pair, kraken_mid,
  kraken_spread_bps, alpaca_mid, divergence_bps, market_session,
  fetched_at, alert_fired}``. Indexed on ``(symbol, fetched_at)`` and
  ``fetched_at`` for the burn-in summary aggregator.

**New scheduler job**: ``kraken_shadow_compare`` runs every 5 min;
guarded by env so cold-start pods stay silent until ops opts in.

**New admin endpoint**: ``GET /api/admin/kraken-shadow/today``
(owner-gated) returns ``{rows, max_bps, p95_bps, divergent_count,
alerts_fired, last_run_at, session_counts, sample, threshold_bps,
enabled}`` for the burn-in card.

**New frontend chip**: ``components/admin/KrakenShadowChip.jsx``
rendered inline in the burn-in panel. Traffic-light status — gray
(disabled / no rows), green (clean), yellow (some symbols above
threshold but no alert fired in regular session), red (alerts fired).

**Universe**: top 20 ML watchlist + full S&P 500 from
``data/sp500_constituents.json`` (~500 tickers). Tunable via
``KRAKEN_SHADOW_TOP_N_ML`` and ``KRAKEN_SHADOW_INCLUDE_SP500``.

**Env knobs** (all optional, safe defaults):
* ``KRAKEN_SHADOW_ENABLED`` (default off)
* ``KRAKEN_SHADOW_DIVERGENCE_BPS_ALERT`` (default 50)
* ``KRAKEN_SHADOW_BATCH_SIZE`` (default 100)
* ``KRAKEN_SHADOW_BATCH_DELAY_SECONDS`` (default 60)
* ``KRAKEN_SHADOW_PAIR_CACHE_TTL_HOURS`` (default 24)
* ``KRAKEN_SHADOW_TOP_N_ML`` (default 20)
* ``KRAKEN_SHADOW_INCLUDE_SP500`` (default true)

**Tests**: 18 cases in ``test_kraken_equity_shadow_service.py``
covering universe loader (top-N + S&P500 toggle), ticker parser
(normal + malformed + zero-mid), divergence math (zero-Alpaca guard),
market-session classifier (RTH / pre / post / weekend / holiday),
pair resolution (fast-path + altname fallback + missing), pair cache
(fresh skips upstream, not-listed sentinel), disabled / no-db
short-circuits, end-to-end tick (3 symbols, mocked HTTP + Alpaca,
correct divergences persisted), zero-Alpaca-quote skip, and
``summarize_today`` aggregator (empty + populated + threshold logic).
**86/86 green** across kraken_shadow + nl_live_bridge +
confidence_gate + natural_language + integrity_mitigation suites.
Lint clean. Backend healthy (200 OK).

**Live verified**:
* ``GET /api/admin/kraken-shadow/today`` (no auth) → HTTP 401.
* ``GET /api/admin/kraken-shadow/today`` (admin cookie) →
  ``{rows: 0, threshold_bps: 50, enabled: false, ...}``.

### NL Live Command Bridge — wire-through to live trading (May 3, 2026)

Follow-up to the Natural Language Trading Layer. Closes the gap between
the advisory NL runtime state and the deterministic production gates.
`SET_RISK_MULTIPLIER` now composes with `integrity_mitigation_service`,
and `SET_MIN_RR` now writes an override doc that `confidence_gate` reads
on every tick.

Module: `services/nl_live_command_bridge.py`
* Two-step flow: `prepare_nl_live_command()` stages a pending row, then
  `confirm_nl_live_command(pending_id, session_id)` applies it.
* 30s confirmation cooldown + 120s pending TTL (Mongo TTL index).
* Integrity-floor respected — NL can tighten sizing, never widen above
  the deterministic throttle ceiling. Preview payload shows the
  operator the capped effective value and a `floor_note` explaining it.
* Idempotent confirms — re-confirming an applied command returns
  `already_applied` instead of double-inserting.
* Session-isolation — only the session that prepared a command can
  confirm it.
* Proof-chain audit trail via `ProofEventType.NL_COMMAND_APPLIED`.

Collections touched:
* `nl_pending_commands` (TTL index on `expires_at`)
* `confidence_gate_overrides` (single `_id: "current"` row, read by
  `confidence_gate.get_dynamic_confidence_threshold`)
* `integrity_mitigations` (via the existing activator)
* `decision_proof_chain` (via `async_append_proof_event`)

`confidence_gate.get_dynamic_confidence_threshold()` now:
* Reads `confidence_gate_overrides._id="current"` on every tick.
* Lifts the base threshold by `min(0.15, (min_rr - 1.5) × 0.10)` when
  active, silently ignores expired rows, and caps at MAX_THRESHOLD.

**Tests**: 13 cases in `test_nl_live_command_bridge.py` covering:
prepare staging (no side effects), integrity-ceiling capping,
confirm routing for both command kinds, idempotency, expiry, session
mismatch, unknown pending_id, confidence_gate override consumption,
confidence_gate ignoring expired overrides, index creation on None-db
and fake-db, no-db structured error path. **173/173 green** across
adjacent suites (nl_live_bridge + confidence_gate + natural_language +
integrity_mitigation + proof_chain + symbol_failure + sovereign_ai).
Lint clean.

### Natural Language Trading Layer (May 3, 2026)

User-supplied drop-in module: deterministic NL trade explanations + bull/bear/Commander
debate text + "why was this rejected?" debug narratives + safe NL command
parsing. Saved verbatim to `services/natural_language_trading.py` with two
small additions over the paste:

* **Owner-gated router** — every endpoint wraps `Depends(_require_owner)`
  so unauthenticated callers can't mutate the in-process NL runtime
  state. Verified live: `curl /api/nl-trading/state` returns HTTP 401
  without auth, returns full state with admin cookie.
* **Registry contract `set_db(db)` stub** — module has no Mongo
  dependency, but the route registry pattern requires it.

**Endpoints** (all `/api/nl-trading/*`, owner-only):
* `POST /explain` — TradeSignal → bull/bear/commander/risk_notes/headline/summary
* `POST /debate` — TradeSignal → debate-only payload
* `POST /why-rejected` — TradeSignal → human-readable rejection report
* `POST /parse-command` — natural-language text → structured `ParsedNLCommand`
* `POST /execute-command` — applies hard-bounded mutations to in-process NL state
* `GET /state` — reads current NL runtime state + audit log

**Hard safety invariants** (pinned by tests):

* NL never executes trades. ``execute_command`` cannot return
  ``trade_id`` / ``order`` / ``fill`` / ``executed`` / ``broker_order_id`` keys.
* `SET_RISK_MULTIPLIER` clamps to `[0.50, 1.25]`.
* `SET_MIN_RR` clamps to `[0.50, 10.0]`.
* `ABSTAIN` action returns `accepted=False`.
* Each engine instance has its own `NLRuntimeState` — no global mutation
  across engines.
* `ParsedNLCommand.value=None` raises ValueError on execute (no implicit defaults).
* Audit log appended on every executed command.

**Architectural notes flagged to operator** (not yet wired):

The module has its own `NLRuntimeState.risk_multiplier` and `min_rr`.
These are **independent from**:
* `integrity_mitigation_service` global Mongo-backed risk_multiplier.
* `confidence_gate.BASE_MIN_CONFIDENCE` static threshold.
* `failure_mode_classifier` RR rules.

Today the NL layer is purely advisory — it doesn't write through to the
deterministic gates. If we want NL commands to actually shift production
behaviour, two follow-ups are needed:
1. `SET_RISK_MULTIPLIER` should call `integrity_mitigation_service.activate_risk_multiplier(...)`.
2. `SET_MIN_RR` should write to the env-backed `confidence_gate` config or an admin-settable Mongo doc the gates read.

This is intentional for now — the module's design rule is "NL never
executes trades". Wiring it through to live config is a separate
operator-confirmed step.

**Tests**: 28 cases in `test_natural_language_trading.py` covering: util
formatters, all 4 verdict branches (APPROVE / MODIFY / REJECT / WATCHLIST),
hard-veto detection, command parser for every CommandAction, clamp
enforcement on execute, audit log append, mode enable/disable round trip,
abstain-not-accepted, no-trade-execution invariant, isolated state per
engine. **181/181 green** across all adjacent suites. Lint clean.

**Live verified**:
* `GET /api/nl-trading/state` → baseline state (admin cookie).
* `POST /api/nl-trading/parse-command {"text":"reduce risk..."}` →
  `SET_RISK_MULTIPLIER value=0.75`.
* `GET /api/nl-trading/state` (no auth) → **HTTP 401** as expected.

### Confidence Gate + Sovereign Adversarial Amplification (May 3, 2026)

Two additive imports from the user's clean-corridor design that closed real
gaps in our existing stack:

**1. Dynamic confidence gate** (`services/confidence_gate.py`)

Replaces the static `_MIN_PAPER_CONFIDENCE = 0.55` floor with a dynamic
threshold that raises the bar when the system is in stress:

| Condition                | Threshold delta |
|--------------------------|-----------------|
| drawdown >= 10%          | +0.05           |
| drawdown >= 15%          | +0.10 (stacks)  |
| calibration_gap >= 8%    | +0.05           |
| loss_streak >= 3         | +0.05           |
| (cap)                    | 0.90 max        |

Drawdown branches stack — at 15% DD we get +0.15. Calibration gap is the
average of two sources: (a) win-rate vs avg-confidence on `paper_trades`
and (b) right-rate vs avg-conviction on resolved `sovereign_decisions` —
giving us both production-trade and shadow-engine calibration evidence.

Wired into BOTH cores:
* **Equity** (`ml_paper_trader`): `max(_MIN_PAPER_CONFIDENCE, dynamic_threshold)`
  becomes the new floor; skip reason logged to activity feed includes the
  dynamic delta + per-branch reasons.
* **Crypto** (`crypto_paper_trader`): tested AFTER the symbol-failure
  penalty has trimmed conviction (so the dynamic gate sees post-penalty
  confidence). HOLD short-circuit returns `reason="below_dynamic_confidence_threshold"`
  with full audit fields.

Live verified: both cores report `threshold=0.70 delta=0.0 reasons=[baseline]`
on a healthy book with no drawdown / no loss streak / no calibration data
yet — exactly the right baseline.

**2. Sovereign genuinely adversarial** (`sovereign_ai_core._strategist_model`)

Previously: `_strategist_model` ignored `SovereignFeatures.strategist_action` /
`strategist_confidence` even though we passed them in. Sovereign was
"running beside" the strategist, not "attacking" it.

Fix: when upstream strategist passes a high-confidence (>=0.70) proposal
that contradicts Sovereign's native compute (LONG vs bearish lean, or
SHORT vs bullish lean), AMPLIFY Sovereign's contradicting vote. The
disagreement itself is information — a confidently-wrong proposal is
worth attacking. Amplification capped at +0.20 so a borderline native
vote can't be flipped to a strong contradiction by upstream confidence
alone.

Pinned by tests: amplification fires only on disagreement, only when
upstream conf >= 0.70, never on neutral native compute, never when sides
agree.

**Items deliberately skipped** from the user's design:
* Hard 0.15 confidence-gap block — would conflict with our existing
  Phase-2 brake's halve-on-disagreement discipline (better to compose,
  not duplicate).
* Unified `run_adversarial_corridor()` refactor — defer until options
  trading or another asset class justifies the abstraction. Current
  pipeline is working and well-tested.
* Inlining liquidity gate into corridor — already covered by
  `failure_mode_classifier` + Sovereign's `_risk_model`; duplicating
  would create two sources of truth.

**Tests**: 17 new cases in `test_confidence_gate_and_amplification.py`
covering: each threshold branch independently, branch composition,
0.90 cap enforcement, baseline empty-DB read, broken-DB safe baseline,
amplification on LONG/SHORT disagreement, no-amplification on agreement,
no-amplification below 0.70 floor, +0.20 cap enforcement, neutral-compute
no-op. **Total 197/197 → 214/214 green** across all adjacent suites.
Lint clean. Backend restarts cleanly.

### Symbol Failure Memory — short-term per-symbol bias loop containment (May 3, 2026)

Closes the persistent-bias-loop hole that surfaced twice on NVDA: the
strategist kept proposing the same direction even after a string of losses
because past failures only flowed into long-term ML training. This service
adds a short-term memory override that reads recent paper_trade outcomes
on every tick and reduces conviction BEFORE Kelly sizing.

**New module**: `services/symbol_failure_memory.py`

* `compute_failure_penalty(recent_losses, misses_7d) -> FailurePenalty` —
  pure function (zero I/O, microseconds). Tested independently.
* `get_failure_penalty(db, symbol, direction, asset_type)` — async Mongo
  reader. Direction-filters the recent-N window; 7-day window stays
  direction-agnostic so cross-direction failure clusters still get caught.
* `FailurePenalty.apply(confidence, size_multiplier)` — clamped to [0, 1].

**Penalty schedule** (env-tunable):

| Recent-N losses | Effect                          |
|-----------------|---------------------------------|
| 0–1             | No penalty                      |
| 2               | confidence × 0.7                |
| 3               | confidence × 0.5                |
| 4+              | **force HOLD** (return None)    |

| 7-day misses    | Effect                          |
|-----------------|---------------------------------|
| 0–1             | No penalty                      |
| 2               | size_multiplier × 0.5           |
| 3+              | confidence capped at 0.75       |

Branches compose: a symbol with 3 recent + 4 in 7d gets confidence × 0.5
AND size × 0.5 AND the 0.75 cap (tightest wins). At 4+ recent the symbol
goes on hard cooldown until a non-loss trade clears the buffer.

**Wired into both cores** (PRD-mode aware):

* `ml_paper_trader.maybe_paper_trade` — runs BEFORE the Sovereign AI
  shadow + Kelly sizing. Force-HOLD short-circuits with
  `log_paper_trade_skipped(reason="symbol_failure_cooldown")`. Persists
  `symbol_failure_penalty` on the `paper_trades` row.
* `crypto_paper_trader._process_one_symbol` — runs BEFORE the
  adversarial-phase resolution. Force-HOLD returns the standard
  `{skipped: true, reason: "symbol_failure_cooldown"}` shape with
  `recent_losses` and `misses_7d` for observability.

**Admin endpoint**: `GET /api/admin/symbol-failure/{symbol}?asset_type=equity|crypto`
— shows the penalty that would apply to the next signal. Owner-only.

**Live verified** (May 3, 2026):

* NVDA equity: 1 recent loss, no penalty active yet (kicks in at ≥2 — correct).
* BTCUSDT crypto: 0 recent losses, baseline state.

**Tests**: 19 new cases in `test_symbol_failure_memory.py` covering: each
penalty tier independently, force-HOLD dominance, branch composition,
clamping to [0, 1], direction filtering on recent-N, 7-day cross-direction
catch, crypto collection routing, null-db safety, exclude open trades,
exclude old (>7d) misses, immutable dataclass. **197/197 green** across
all adjacent suites. Lint clean.

**Why this fix is high-impact, low-complexity**: the existing pipeline
stored failures but did not strongly influence the next decision. Now:
recent failures → immediately reduce conviction. The Commander brake
phase 2 already disagrees-and-halves on adversarial verdict mismatch;
this layer adds a complementary single-asset memory penalty so the
strategist can't hammer the same losing thesis tick after tick.

### Sovereign AI P1+P2 — Resolution Loop, Proof Chain, Burn-in Chip, Narrator (May 3, 2026)

Closes the P1 safety + P2 observability work on Sovereign AI in a single batch.

**1. Resolution loop** — `services/sovereign_resolution_loop.py`

Scheduled every 15 min (APScheduler `sovereign_resolution_tick` job). For
every unresolved sovereign decision aged past its horizon (`60m` / `4h` /
`eod` = 8h), joins against `paper_trades` (equity) or `crypto_paper_trades`
(crypto) via the `sovereign_decision_id` link and back-patches
`outcomes.{horizon}` with `{pnl_pct, was_right, resolved_at}`.

Per-horizon batch cap 50 so a backlog can't starve the fleet. Idempotent
— re-running on the same `(decision_id, horizon)` is a no-op. Per operator
policy: resolves ONLY decisions with a linked fired trade (HOLD decisions
stay unresolved; they don't contribute to the promotion gate threshold).

Admin endpoint: `POST /api/admin/sovereign-ai/resolution/tick` — manual
trigger for smoke tests. Live-verified 2026-05-03: empty baseline tick
returns clean `status=ok` with 6 per-horizon zero rows.

**2. Proof-chain audit** — extension to `services/proof_chain.py`

Added `ProofEventType.SOVEREIGN_DECISION`. Every call into `sovereign_decide`
that persists to Mongo now also appends a tamper-evident proof block
carrying `decision_id`, `action`, `confidence`, `conviction_tier`,
`size_multiplier`, `reasons`, `vetoes`, `model_votes`, `advisory_votes`,
`calibration_score`, `shadow`. Entity ID is
`sovereign:{symbol}:{decision_id[:8]}`; actor tag
`sovereign_ai[{equity|crypto}]`.

This completes the audit trail requirement from the previous session:
"full audit trail, every Commander/Strategist/Council opinion captured
even when Sovereign overrules" — those advisory votes are preserved in
both `sovereign_decisions.advisory_votes` AND the proof chain payload,
cryptographically chained.

Proof-chain write failure is logged at DEBUG and swallowed — a broken
audit write cannot block the decision path.

**3. Burn-in dashboard chip** — `GET /api/admin/sovereign-ai/burn-in`

Per-core 24h rollup: `decisions_24h`, `resolved_24h`, `resolved_pct`,
`avg_confidence`, `avg_calibration`, `trades_24h`,
`contribution_applied_24h`, `contribution_applied_pct`, `phase`,
`promoted`, `rows_to_go`.

Frontend `NewsShockBurnIn.jsx` extended with a dedicated Sovereign AI
section (indigo background, `data-testid="sovereign-ai-burn-in"`)
showing 4 chips per core: decisions-24h + gate progress with
traffic-light coloring (green = promoted, yellow = collecting data, gray
= idle). Polls in parallel with the existing NEWS_SHOCK fetch so there's
zero extra latency on the dashboard.

Live data captured at burn-in time:

* Equity core: 1 decision, 227 paper trades, avg conf 50%, 500 rows to go
* Crypto core: 8 decisions, avg calibration 74.94%, avg conf 44.74%

**4. AI-to-AI Narrator** — `services/sovereign_narrator.py` + `POST /api/admin/sovereign-ai/narrate/{decision_id}`

Translates a sovereign decision + its 6 model votes + advisory-vote delta
into a 3-bullet plain-English explanation operators can actually read. Uses
**GPT-5.2** via the Emergent universal key. Each narration cached 24h per
decision_id in `sovereign_narrations` so identical renders are a single
paid LLM call. ~$0.001 per first-time narration; subsequent hits are free.

Graceful fallback: when the key is missing / LLM call fails / response
isn't parseable as 3 bullets, the function returns a deterministic
structured fallback built from the raw `model_votes` dict. Never raises.

**Live narrator sample** (LINK crypto decision):

> - Sovereign decision: HOLD LINK at 37.35% confidence (low tier) with
>   size multiplier 0.15; strongest driving signal is the strategist
>   vote HOLD with 0.3735 confidence (bull 0.00 vs bear 0.1265).
> - Advisory AIs: strategist/commander advisory also indicated HOLD but
>   with 0.0 confidence; Sovereign agreed on direction (HOLD) and was
>   not overridden by any other lane signals.
> - Watch factor: no vetoes are active and catalyst/event risk is marked
>   normal (delta 0.0, sentiment 0.0), so the key risk is sudden
>   regime/catalyst change given regime is neutral (gate 0.6) and
>   options flow provides no confirming signal.

This realizes the user's "communicate with other AIs in a way that humans
understand" vision — operator opens the narration endpoint during a trade
review and immediately sees *why* Sovereign shaded conviction up or down,
in natural prose backed by the exact numbers.

**Tests**: 18 new cases in `test_sovereign_resolution_narrator.py`
covering: `_was_right` directional matrix including synonyms, resolution
back-patches fired trade at 60m, skips young decisions below horizon,
skips HOLD decisions with no linked trade, idempotent re-run, crypto
path, null-db short-circuit, narrator fallback always returns 3 bullets,
fallback flags vetoes, missing-decision error, cache round-trip (second
call returns `cached=true`), null-db error. **Total 48 new Sovereign AI
tests** across the 3 test files; **178/178 green** across all adjacent
suites. Lint clean on all 7 new/modified files.

**Deferred (explicit)**: XGBoost / LightGBM retraining — the
`run_dtd_nightly_retrain` placeholder emits the correct stats payload
(total/resolved per asset), but real model training lands when the first
500 resolved shadow rows are on disk (currently 0 resolved; ~200-400
hours of trading at observed cadence).

### Sovereign AI — DTD/PRD Dual-Stack Mode Guard (May 3, 2026)

Wraps the shared Sovereign AI brain with strict DTD (research / training /
challenger) vs PRD (production / shadow + bounded contribution) adapters so
the same ``sovereign_ai_core.py`` brain can't accidentally execute training
code against a live production DB.

**New modules**

* ``services/sovereign_mode_guard.py`` — ``RISEDUAL_CORE_MODE`` env toggle
  (``PRD`` default), ``require_dtd()`` / ``require_prd()`` hard-fail
  sentinels, ``is_dtd()`` / ``is_prd()`` helpers. Unknown values fall back
  to ``PRD`` — safer default for accidental deploys.
* ``services/sovereign_dtd_adapter.py`` — DTD-only entry points
  (``run_dtd_challenger_decision``, ``run_dtd_nightly_retrain``). Every
  call asserts ``require_dtd()`` so PRD pods can never execute training.
* ``services/sovereign_prd_adapter.py`` — PRD-mode entry points
  (``run_prd_sovereign_shadow``, ``apply_promoted_sovereign_contribution``,
  ``assert_prd_never_trains``). Shadow logging is intentionally mode-agnostic
  (observation is always safe); contribution is guarded behind
  ``require_prd()``.

**Bounded contribution — the critical safety rail**

``sovereign_promotion_gate.apply_sovereign_contribution(...)`` pins the
invariants the user flagged as "without this it will be a disaster":

| Scenario | Behaviour |
|---|---|
| Sovereign not promoted | Confidence returned unchanged. ``reason="not_promoted"``. |
| No sovereign decision available | Unchanged. ``reason="no_sovereign_decision"``. |
| Production action = HOLD | Unchanged. ``reason="production_hold_immutable"``. **Sovereign CANNOT turn a HOLD into a trade.** |
| Sovereign agrees with production direction | Confidence bumped. Capped at ``MAX_SOVEREIGN_CONFIDENCE_DELTA`` (default 0.08, env-tunable). |
| Sovereign disagrees with production direction | Confidence REDUCED. Capped at the same delta. **Sovereign CANNOT flip the action** — it may only soften conviction. |
| Sovereign says HOLD while production trades | Confidence softened proportional to sovereign conviction. |

The returned meta block carries ``applied`` / ``reason`` / ``delta`` /
``sovereign_action`` / ``sovereign_confidence`` — never an ``action``
field, by design, so the contribution cannot leak a direction override
upstream.

**Paper-trader wire-ups updated**

Both ``ml_paper_trader`` and ``crypto_paper_trader`` now call
``run_prd_sovereign_shadow()`` + ``apply_promoted_sovereign_contribution()``
BEFORE position sizing — so once the gate unlocks, the Kelly / crypto
notional calc naturally uses the nudged confidence. A comment pin in both
cores reminds future contributors:

    # CRITICAL: confidence-only mutation. Never touch direction.

The ``paper_trades`` rows gain a ``sovereign_contribution`` block alongside
the existing ``sovereign_decision_id`` so an operator can inspect how often
sovereign actually moved the needle.

**Admin endpoints added**

* ``GET /api/admin/sovereign-ai/mode`` — reports current ``RISEDUAL_CORE_MODE``.
* ``POST /api/admin/sovereign-ai/dtd/nightly-retrain`` — DTD-guarded, returns
  HTTP 403 in PRD mode (live-verified).

**Tests**: 22 new cases in ``tests/test_sovereign_mode_guard.py``
covering: default mode is PRD, DTD/PRD cross-guards, each bounded
contribution branch (not_promoted / no_decision / HOLD_immutable /
aligned_bump / contra_reduces / sovereign_hold_softens), cap
enforcement under env override, confidence clamping, malformed-input
safe no-op, DTD nightly blocks in PRD, PRD contribution blocks in DTD,
PRD shadow logging works in BOTH modes. Plus 30 original Sovereign AI
Core tests. **175/175 green** across all adjacent suites.

**Live verified** (2026-05-03):

* ``GET /api/admin/sovereign-ai/mode`` → ``{"mode":"PRD","is_prd":true}``.
* ``POST /api/admin/sovereign-ai/dtd/nightly-retrain`` → HTTP 403
  ``"DTD-only operation blocked (current mode: PRD)"``.
* Status endpoint shows both cores in ``phase_1_shadow_only`` with
  ``rows_to_go: 500``.

### Sovereign AI Core — Shadow Engine LIVE on both cores (May 3, 2026)

Ships the first independent, non-LLM decision engine for RISEDUAL. Runs in
**shadow mode** on both the equity core (`ml_paper_trader`) and the crypto
core (`crypto_paper_trader`), writing every decision to
``sovereign_decisions`` without touching live order flow. Once the per-core
promotion gate unlocks (≥ 500 resolved rows at ≥ 70% win rate AND ≥ 65%
calibration), Sovereign AI becomes the sizing/direction authority for that
core; Commander / Strategist / Council outputs then fall back to **advisory
votes** logged on the decision row (no sizing control, no veto power).

**New modules**

* ``services/sovereign_ai_core.py`` — Pure deterministic engine with 6
  native sub-models: Strategist (RSI + momentum → direction), Regime
  (tradeable-regime gate → size multiplier), OptionsIntent (flow skew
  alignment), Catalyst (news-shock / event-risk veto + delta), Risk
  (dollar-volume starvation + ATR extremes + vol-zscore), Calibration
  (self-consistency score). Zero LLM calls, zero network I/O, microseconds
  per tick. ``asdict(features)`` is stored verbatim on
  ``feature_snapshot`` so ML replay is faithful. Hard-veto supremacy —
  ``NEWS_SHOCK_RESTRICTED`` / ``LIQUIDITY_TRAP`` / ``VOLATILITY_EXTREME``
  force ``HOLD`` + ``size_multiplier=0`` no matter what. Any internal
  exception → safe ``HOLD`` + ``SOVEREIGN_INTERNAL_ERROR`` veto so the
  paper-traders are never blocked.
* ``services/sovereign_promotion_gate.py`` — Per-core gate math mirroring
  ``equity_shadow_promotion`` shape. Env-tunable
  (``SOVEREIGN_PROMOTE_MIN_ROWS`` / ``_MIN_RATE`` / ``_MIN_CAL`` /
  ``DEMOTE_BELOW`` / ``DEMOTE_WINDOW_DAYS``). Rolling 30-day demotion
  watcher triggers automatic revert-to-shadow if live authority win rate
  falls below 55%.
* ``routes/sovereign_ai.py`` — Owner-gated admin endpoints:
  ``GET /api/admin/sovereign-ai/status`` (per-core promotion snapshot +
  last 10 decisions each), ``POST /decide/{symbol}`` (manual tick for
  smoke tests), ``POST /resolve/{decision_id}`` (manual back-patch),
  ``POST /ensure-indexes`` (idempotent Mongo index creation).

**Wiring into both cores**

* **Equity core** (``services/ml_paper_trader.maybe_paper_trade``) — after
  the Commander Phase 2 brake block, calls ``run_shadow_for_equity(...)``.
  Pulls features from the FeaturesSnapshot + catalyst snapshot. Any
  failure is swallowed; ``sovereign_decision_id`` is stamped on the
  ``paper_trades`` row when available for future back-patching.
* **Crypto core** (``services/crypto_paper_trader._process_one_symbol``)
  — before the final HOLD short-circuit (so both fired and declined
  trades generate training rows), calls ``run_shadow_for_crypto(...)``
  with the strategist signal dict. Same fail-silent discipline.

**Advisory-vote capture**

Both wire-ups pass an ``advisory_votes`` dict containing Strategist +
Commander verdicts. Post-promotion, when Sovereign AI is the authority,
these votes still land in ``sovereign_decisions.advisory_votes`` — full
audit trail of every AI opinion, even when Sovereign overrules them.
Matches the user's request: "the other decisions can act as a learning
experience for RISEDUAL... communicate with other AIs in a way that
humans understand."

**Safety invariants (pinned by tests)**

* Hard vetoes (`LIQUIDITY_TRAP`, `NEWS_SHOCK_RESTRICTED`,
  `VOLATILITY_EXTREME`) ALWAYS win — Sovereign cannot override even
  post-promotion.
* Any exception in ``sovereign_decide`` returns safe ``HOLD`` +
  ``size_multiplier=0`` rather than raising.
* Mongo writes never leak ``_id`` (coordinator creates fresh dicts via
  ``asdict`` + ``to_mongo_doc``; never spreads Mongo-mutated documents
  into the response).
* Fail-safe default for ``is_sovereign_authority()`` is ``False`` — any
  gate-read failure keeps Commander in charge.
* Promotion gate never raises — BrokenDB test pins this.

**Live verified** (Sat 2026-05-03):

* Backend restarted cleanly; sovereign collection indexes created.
* ``/status`` returns correct empty-state snapshot for both cores
  (``rows_to_go: 500``, ``phase: phase_1_shadow_only``).
* ``/decide/AAPL`` produces shaped decision with all 6 model votes +
  clamped confidence + HOLD fallback (no bars, so strategist is neutral,
  regime unknown → conservative size_multiplier).

**Tests**: 30 new cases in ``tests/test_sovereign_ai_core.py``
covering: each pure sub-model's happy path + edge cases, coordinator
veto supremacy, feature-snapshot round-trip, internal-error fallback,
confidence/multiplier clamping, options contra-alignment reducing
confidence, resolution idempotency, promotion gate below-threshold,
above-threshold, rolling-30d demotion, Mongo-error fail-safe.
**153/153 green** across all adjacent Sovereign + Commander-brake +
catalyst + news-shock + smart-money + feeder + scheduler suites.
Lint clean on all 4 new files.

**Deferred to P1 (next iteration)**

* Authority handoff: when ``is_sovereign_authority(db, asset_type)``
  returns True, the paper-traders read Sovereign AI's verdict as primary
  and treat Commander output as advisory only.
* Resolution loop: back-patches outcomes at 60m / 4h / EOD horizons
  against ``sovereign_decisions.outcomes.{horizon}``.

**Deferred to P2** (> 500 shadow rows collected)

* XGBoost/LightGBM retraining endpoint.
* Sovereign AI chip on the ``/burn-in`` dashboard.
* Human-readable "AI-to-AI communication" narrator — bullet-point
  explanation of why Sovereign agreed/disagreed with Commander (user's
  feature request).

### Reuters removal + frontend /burn-in dashboard card (May 2, 2026)

**Reuters removed from 3 scrapers**:
* ``services/world_events_service.py`` — 2 Reuters RSS feeds dropped
  from the 6-source list. AP News also dropped (same DNS-unresolvable
  behaviour in this environment). Remaining: BBC / NYT / CNBC.
* ``services/headlines_pipeline.py`` — Reuters removed from
  ``DEFAULT_SOURCES``. 7 sources remaining: CNBC / MarketWatch /
  Fox Business / WSJ / Bloomberg / Yahoo Finance / Investing.com.
* ``services/financial_scraping_service.py`` — ``_scrape_reuters``
  method deleted + dispatch call removed from ``scrape_financial_news``.
  Fully duplicative with CNBC / WSJ / Bloomberg; no unique coverage
  lost.

Backend logs now free of ``feeds.reuters.com DNS`` spam. Post-restart
log tail: **0 Reuters error entries**.

**Frontend burn-in card** (``components/admin/NewsShockBurnIn.jsx``):

* 60-second polling interval against ``GET /api/admin/news-shock/burn-in``
* Six traffic-light chips — one per operator signal:
    1. Scheduler (offset + last-updated freshness)
    2. Catalyst Events (total + latest symbol + age)
    3. News Telemetry (rows + last symbol + volume)
    4. Snapshots (total + zscore_ready count)
    5. Smart-Money 24 h (proof-chain block count)
    6. Equity Telemetry (symbols tracked + $vol coverage)
* Freshness-based colour mapping: <20 min = green, <2 h = yellow,
  >2 h = red, no data = gray. Matches the operator's on-tick cadence
  (15 min) so a healthy tick stays green between refreshes.
* Shows top-3 most-recent catalyst snapshots with shock_state +
  event_risk so glance-at-screen gives the operator the "what's
  moving right now" answer.
* Manual refresh button for impatient polling.
* Accessible test IDs: ``news-shock-burn-in`` (card),
  ``burn-in-chip-*`` (per chip), ``burn-in-refresh`` (button).

Registered as a new tab in ``AdminPanel.jsx`` — ``Burn-In`` with the
``Radio`` icon, sits between ``Health`` and ``Providers``. Subtitle
string + content switch added to the existing dispatch map.

**Verification**

* Lint clean on all 5 touched files (Python + JSX).
* Live curl of ``/burn-in`` returns 7-key shape exactly matching
  the component's polling expectations
  (``equity_telemetry.total_symbols_tracked: 200`` proves the
  ``_warm_one`` dollar-volume feeder is already populating from
  Saturday's cycle).
* 218/218 tests still green in the NEWS_SHOCK + feeder + terminal
  suite.

### Commander pipeline catalyst wiring LIVE (May 2, 2026)

Final hand-off from the Phase C drop-in: `apply_catalyst_context`
is now invoked inside the real Commander orchestrator AND the
equity paper-trader's inline adversarial pass. Catalyst annotations
now flow into every live bull/bear case produced by the
application.

**1. `adversarial_core.run_adversarial_decision` — canonical wiring**

Added between the `bull_agent` / `bear_agent` runs and
`resolve_adversarial`:

```python
catalyst_snapshot = await db.catalyst_snapshots.find_one(
    {"symbol": symbol}, {"_id": 0},
)
bull, bear = apply_catalyst_context(bull, bear, catalyst_snapshot)
```

Wrapped in a bare `try/except: pass` — the invariant is **no
catalyst read failure can suppress the decision**. Commander's
core authority stays intact; a broken snapshot just loses the
annotation this tick.

This is the orchestrator used by `crypto_paper_trader` +
`research_shadow_engines`, so both crypto entries and the shadow
research stream now carry catalyst narrative automatically.

**2. `ml_paper_trader.maybe_paper_trade` — equity path same wiring**

The equity paper-trader builds its adversarial context inline
(not through `run_adversarial_decision`), so the same 3-line
catalyst enrichment was mirrored there — symbol pulled from
`ticker.upper()`. Same fail-safe discipline.

**3. Tests** (2 new in `test_catalyst_wiring.py`)

* ``test_run_adversarial_decision_passes_catalyst_through_when_gates_open``
  — opens both gates (env flag + Tier 3 probe), feeds a bullish
  catalyst snapshot, asserts the returned ``bull_case.thesis``
  carries ``bullish_catalyst_sentiment`` and the bear thesis is
  untouched.
* ``test_run_adversarial_decision_silent_on_catalyst_read_error``
  — patches ``catalyst_snapshots.find_one`` to raise
  ``RuntimeError``, asserts the decision payload is still returned
  intact. Pins the "broken catalyst read cannot suppress
  Commander" invariant.

**Tests**: 19/19 catalyst + 218/218 NEWS_SHOCK/feeder/terminal
suites + 190/190 adversarial/conviction/commander suites — zero
regression. Lint clean on all 3 touched files. Backend restarted
cleanly.

### Catalyst wiring (conviction + Commander) + Monday burn-in observability (May 2, 2026)

Completes the operator's Phase C drop-in: wires the catalyst helpers
into the actual compute functions AND adds a single aggregated
burn-in endpoint so the Monday AM readiness check is one curl, not
ten.

**1. ``conviction_service`` — catalyst adjustment live**

* New pure helper ``_catalyst_conviction_delta(action, snapshot)``
  returning ``(delta, reason_code)``. Implements the operator spec
  verbatim: ``-0.10`` on high, ``+0.05`` on elevated-aligned,
  ``-0.05`` on elevated-unaligned, ``0.0`` otherwise. Action
  synonyms (``BUY``/``UP``/``LONG`` and ``SELL``/``DOWN``/``SHORT``)
  all map to the right alignment semantics.
* Wired into ``compute_conviction`` as the new **component #7**
  after options-flow boost. Reads ``catalyst_snapshots`` async
  (``find_one`` by symbol); any lookup error swallows to 0.0 and
  ``reason="NO_CATALYST_DATA"`` so the existing compute path is
  pristine when the collection is empty. Composite score still
  clamped to ``[0, 1]`` after addition — a catalyst can never drag
  below neutral or above perfection.
* The breakdown dict in the return value gains
  ``catalyst_adjustment`` + ``catalyst_reason`` alongside the
  existing components so every conviction row carries full
  transparency.

**2. ``adversarial_core`` — catalyst narrative enrichment**

Mirrors the existing ``apply_options_context`` pattern — additive
only, never touches ``confidence`` / ``expected_r``:

* ``apply_catalyst_context(bull, bear, snapshot)`` — appends
  ``| bullish_catalyst_sentiment`` / ``| bearish_catalyst_sentiment``
  to the matching thesis. Elevated/high shock state appends
  ``| news_shock_<state>_z<z>`` to **both** theses (shock raises
  uncertainty on EITHER side — an operator going long against a
  shock should see the same flag as one going short against it).
  Returns fresh ``AgentOutput`` instances via ``dataclasses.replace``;
  inputs are never mutated.
* ``catalyst_thesis_lines(snapshot)`` — structured variant
  returning ``{bull, bear, risk}`` lists. Used by callers that
  want the raw strings (Terminal UI, proof chain) rather than the
  in-string annotation.

**3. Monday burn-in endpoint**

``GET /api/admin/news-shock/burn-in`` — one-shot aggregated health
check. Returns six independent signals:

1. ``scheduler.last_offset`` + ``last_updated_at`` — is the 15-min
   tick running?
2. ``catalyst_events`` total + latest event time — is the feeder
   persisting articles?
3. ``news_telemetry`` rows — is the shock-compute step recording
   baselines?
4. ``catalyst_snapshots`` total + ``zscore_ready`` + 3 most recent
   updates — is the projection landing?
5. ``smart_money_blocks_24h`` — are ``SMART_MONEY_VERIFIED`` proof
   blocks appearing on real equity decisions?
6. ``equity_telemetry`` total symbols + per-symbol sample with
   ``has_dollar_volume`` / ``has_news_count`` / ``has_news_sentiment``
   flags — is ``_warm_one`` feeding the liquidity baselines?

Each read is a single collection count or capped find — safe to
poll every 30s during the burn-in window.

**Live verification** (Sat 2026-05-02 21:34 UTC):

* `/burn-in` returns a clean all-systems snapshot.
* Equity telemetry already has **200 symbols tracked** with
  ``has_dollar_volume=True`` on NVDA/TSLA/MSFT/AMZN — the
  ``_warm_one`` dollar-volume feeder fired on Saturday's post-close
  warm as designed.
* News/sentiment fields correctly empty — feeders wait for
  Monday's market-hours gate.

**Tests**: 16 new ``test_catalyst_wiring.py`` cases pinning every
delta branch + alignment synonym + narrative enrichment invariant
(bull-only / bear-only extension, both-sides on shock,
confidence/expected_r untouched). 147 adversarial+conviction tests
still pass — no regression in the existing pipelines.

169/169 green across the NEWS_SHOCK / feeder / scheduler / terminal
suites. Lint clean on all 4 touched files.

### Phase C — NEWS_SHOCK catalyst layer (May 2, 2026)

Implements the operator's full drop-in spec for a statistical
catalyst layer on top of the existing Benzinga + AV feeders.
Converts the spec's sync-pymongo code to async Motor; wires
catalyst_events persistence into both feeders; adds the NEWS_SHOCK
gate integration points at the Terminal, failure-mode classifier,
and admin dashboard.

**Operating rules (pinned by tests)**

NEWS_SHOCK may:
* annotate Commander / Terminal (narrative + risk chip)
* reduce size via ``catalyst_risk_gate`` (0.75× aligned / 0.50×
  unaligned on elevated; 0× on restricted)
* slightly adjust conviction (±0.05–0.10 — helper ready, not yet
  wired into the existing conviction computation)

NEWS_SHOCK may NOT:
* create a new BUY / SELL
* flip direction
* override stricter hard vetoes (LOW_RR, LIQUIDITY_TRAP,
  CIRCUIT_BREAKER)
* fire on fewer than 20 baseline samples

**New modules**

* ``services/news_shock_service.py`` — async Motor conversion of the
  spec's pymongo core. Pure statistical functions (``zscore``,
  ``classify_sentiment``, ``classify_news_shock``,
  ``compute_sentiment_score``, ``compute_news_volume``) + two
  async Mongo readers (``fetch_recent_events``,
  ``fetch_baseline_volumes``) + ``compute_news_shock_for_symbol`` /
  ``compute_news_shock_batch`` coordinators.
  ``MIN_BASELINE_SAMPLES=20`` pins the no-false-positive window —
  ~20 trading windows after first ingest a symbol goes live. Zero
  stddev degrades to ``None`` (no divide-by-zero footgun).
* ``services/catalyst_snapshot_service.py`` — projects raw shock
  states onto ``catalyst_snapshots`` (single doc per symbol,
  upserted). Owns the ``event_risk`` derivation (``restricted`` /
  ``elevated`` / ``normal``).
* ``services/catalyst_risk_gate.py`` — pure decision function
  (signal + snapshot → allow / size_multiplier / reason). 12 test
  cases pin all 4 branches + synonym boundaries.

**Persistence wire-ups (the spec had to be patched into async)**

* ``news_shock_feeder._persist_catalyst_events`` — every Benzinga
  fetch now upserts articles into ``catalyst_events`` with
  ``event_id="benzinga:<id>"`` for idempotency. No sentiment
  (free-tier unavailable) — Benzinga owns volume enrichment.
* ``av_sentiment_feeder._persist_av_catalyst_events`` — every AV
  fetch persists articles with ``event_id="av:<url>"`` and
  ``sentiment_score`` in [-1, 1]. AV owns sentiment enrichment.
  Both feeders write to the same ``catalyst_events`` collection;
  the compute step merges across sources automatically.
* ``news_feeders_scheduler.run_news_feeders_tick`` — after both
  feeders complete, calls
  ``refresh_catalyst_snapshots(db, symbols)`` so every tick that
  ingested articles also refreshes the snapshot projection.
  Failure-isolated (snapshot refresh crash can't freeze the
  rotation offset).

**Classifier / Terminal / admin integration**

* ``failure_mode_classifier.classify_catalyst_failure`` — new
  helper that maps a catalyst snapshot to ``FailureModeResult(
  mode=NEWS_SHOCK, block_trade=True, …)`` ONLY when
  ``event_risk="restricted"`` or ``shock_state="high"``. Used
  alongside the existing MarketTelemetry-driven path via
  ``pick_tighter_failure``; hard safety vetoes still win.
* ``terminal_aggregator.get_signal`` now reads ``catalyst_snapshots``
  and emits:
    * a ``catalyst`` block with ``event_risk``, ``news_shock``, and
      a ``headline_chip`` for UI;
    * new catalyst entries in the risk list
      (``News shock restricted`` high-severity,
      ``Catalyst risk elevated`` medium,
      ``News sentiment: <label>`` low).
* ``GET /api/admin/news-shock/status`` — per-symbol readiness
  dashboard (tracked / ready / elevated / high counts + the most
  recent headline). Capped at 300 rows.
* ``POST /api/admin/news-shock/ensure-indexes`` — one-shot
  idempotent helper to create the 4 Mongo indexes
  (``catalyst_events.{symbol,event_time}``,
  ``catalyst_events.event_id_unique``,
  ``news_telemetry.{symbol,created_at}``,
  ``catalyst_snapshots.symbol_unique``). Ran once live — all 4
  indexes created.

**Deliberately deferred**

* ``conviction_service.apply_catalyst_adjustment`` helper — designed
  per spec but not wired into the existing conviction computation
  yet. The existing compute function is large and complex; pinning
  the exact injection point needs operator review. Helper available
  for drop-in when ready.
* ``adversarial_core.catalyst_thesis_lines`` — same reason. The
  bull/bear case helpers exist in ``adversarial_core`` but have
  their own narrative injection sequence; adding catalyst lines
  without understanding ordering could break existing Commander
  output shape.

**Live verification** (2026-05-02):

Benzinga smoke fed 2 AAPL articles → ``catalyst_events`` now has
2 rows with stable ``benzinga:{id}`` keys. Indexes created via
admin endpoint. ``/news-shock/status`` returns correct empty-state
JSON (no snapshots yet since Saturday). Mon 13:00 UTC the
scheduler fires and the baseline starts accumulating.

22 new tests (``test_news_shock_service.py``) pinning every pure
function in the spec + 4 catalyst-gate branches. 171/171 across
adjacent suites. Lint clean on all 10 touched files. Backend
restarted cleanly (491 routes).

### Scheduler wiring + Smart Money live + dollar_volume telemetry feeder (May 2, 2026)

Ships three interlocking bits that turn the previously-built
infrastructure into auto-running production code.

**1. News feeders scheduler** (``services/news_feeders_scheduler.py``)

APScheduler job registered in ``server.py`` firing every 15 min.
Each tick:

* Checks a **market-hours gate** — Mon-Fri, 13:00-21:00 UTC (a
  conservative superset of the 9:30-16:00 ET cash session that
  sidesteps DST boundary bugs entirely). Weekends / off-hours →
  no-op with ``status="skipped"``; zero provider calls consumed.
* Pulls the next 15 Tier A symbols via a **rotation offset**
  persisted in ``scheduler_state.news_feeders_rotation``. After
  ~7 ticks a full 100-symbol Tier A sweep completes; each symbol
  gets ~3-4 samples per market day — exactly the minimum the
  ``_baseline`` helper needs to start computing z-scores.
* Feeds **both** ``batch_feed_symbols`` (Benzinga) and
  ``batch_feed_sentiment`` (AV) back-to-back on the same batch so
  the two halves of ``NEWS_SHOCK`` share a baseline cadence.
* Persists the new offset **only after both feeders complete** —
  mid-tick crashes re-feed the same slice next tick (idempotent
  feeders make this safe; the alternative of advancing eagerly
  would skip symbols on crash).

**Budget math**: 15 symbols × 2 providers × ~26 ticks/day = ~780
calls/day split evenly — 390 Benzinga (78% of 500 ceiling), 390
AV (65% of 600 ceiling). Plenty of headroom for ad-hoc operator
probes via the existing smoke endpoints.

**Admin endpoint**: ``POST /api/admin/news-feeders/tick`` — manual
trigger for smoke-testing without waiting for the next cron firing.

11 tests pin the market-hours gate (weekday/weekend/edges),
rotation math (clean slice + wrap-around), empty-Tier-A short
circuit, feeder-failure resilience (offset still advances so a
broken provider doesn't freeze the rotation).

**2. Smart Money Verification live** (``risedual_ip_logic.py``)

Added Step 6.5 between the Patent M failure-mode block and the
Patent I risk-budget step. For equity decisions only:

* Imports ``smart_money_verification.verify_and_append``
* Pulls the raw Mongo handle off
  ``AsyncMongoProofChainStore._db`` (new attribute on the store —
  cheaper than plumbing a second Mongo reference through every
  context)
* Writes a ``SMART_MONEY_VERIFIED`` proof block with the 13F
  alignment result (``confirms`` / ``contradicts`` / ``neutral`` /
  ``no_data``) alongside the strategist action

Never blocks a trade — IP contract keeps full authority via the
classifier + risk budget. Any exception is logged at DEBUG and
swallowed (verification is a nice-to-have audit block, a broken
SEC EDGAR endpoint can't take down order entry).

Crypto + options decisions skip this step — 13F data has no signal
on non-equity assets. Options flow verification will come through
a separate (future) block type.

**3. ``dollar_volume`` telemetry feeder** (``top_universe_service.py``)

Found during this work: nothing was calling
``equity_telemetry.record_measurement`` for atr/spread/volume/
dollar_volume — the rolling baselines were entirely empty.

Added a record hook inside ``_warm_one`` that fires once per warm
cycle per symbol. Piggybacks on ``bars`` (already fetched for
technicals computation) — **zero additional API calls**.
Computes:

* ``atr_pct`` = ``tech["atr_pct"]`` (if technicals ran)
* ``volume`` = latest close-of-day volume
* ``dollar_volume`` = ``close × volume`` (spot reading,
  NOT the 20-day mean already stamped on the universe row —
  we want the instantaneous value so ``get_telemetry(current_
  dollar_volume=…)`` has something real to compare the rolling
  baseline against)

Runs twice daily (post-close 21:05 + pre-open 13:00 UTC) per
existing ``top_universe`` warm schedule. After ~3 cycles each
Tier A symbol has enough history for z-scores; the dollar-volume
starvation branch of ``LIQUIDITY_TRAP`` becomes live.

**Totals**: 11 new scheduler tests + 181/181 across all adjacent
suites. Lint clean (5 files). Backend restarted cleanly (491
routes, +8 from last checkpoint). Live-verified: Saturday tick
correctly skipped with ``outside_market_hours``. Mon-Fri 13:00 UTC
onward the scheduler runs auto-populated.

### Benzinga News Feeder → NEWS_SHOCK gate wired (May 2, 2026)

Completes Phase B of the Benzinga rollout: the feeder pipeline from
Benzinga News API → ``equity_telemetry`` rolling buffer →
``MarketTelemetry.news_volume_zscore`` → Patent M's ``NEWS_SHOCK``
branch. The dormant branch is now wired to a real data source for
the first time.

**1. Telemetry buffer extension** (``services/equity_telemetry.py``)

* ``record_measurement`` now accepts ``news_count`` and
  ``news_sentiment_abs`` (both optional, both tolerate zero —
  ``news_count=0`` is a legitimate quiet-window sample, not an
  error).
* ``get_telemetry`` now computes ``news_volume_zscore`` against the
  rolling ``news_count`` baseline + stdev (same statistical shape as
  ``volume_zscore`` — the classifier's existing 3.0σ threshold
  applies unchanged). Populates the already-existing
  ``MarketTelemetry.news_volume_zscore`` / ``news_sentiment_abs``
  fields that previously surfaced as 0.0 at every call site.
* Baseline-bootstrap safe: the existing ``_baseline`` three-sample
  minimum keeps the classifier dormant on fresh-deploy symbols.

**2. Feeder service** (``services/news_shock_feeder.py``)

* ``fetch_and_record_news_telemetry(db, symbol, window_minutes=30)``
  — single-symbol coordinator: calls
  ``benzinga_news_service.fetch_news`` → counts recent articles via
  the pure ``count_recent_articles`` helper → records via
  ``equity_telemetry.record_measurement``. Returns a dict with the
  count, total returned, ``recorded`` flag, and the raw Benzinga
  meta block for observability.
* ``batch_feed_symbols(db, symbols)`` — iterates through a list,
  serialized by the 2 s rate-limit lock inside the Benzinga client.
  Short-circuits the loop when the daily ceiling trips (no point in
  serializing through 50 more calls that will all bounce).
* Fail-safe: disabled key, daily-ceiling hit, upstream 5xx /
  network failure → skip the record (baseline stays time-consistent
  and never biased toward zero by outages). Legit zero-article
  windows ARE recorded.

**3. Admin endpoints** (owner-only)

* ``POST /api/admin/benzinga/news-telemetry/{symbol}`` — single
  symbol feeder. Consumes 1 call from the daily ceiling.
* ``POST /api/admin/benzinga/news-telemetry-batch?symbols=A,B,C``
  — batch feeder. 20-symbol limit per call (bounds wall-time at
  ~40 s worst case).

**4. Tests** (18 new)

* 9 in ``test_news_shock_feeder.py``: happy path records the count,
  zero-articles legit-records as 0, disabled / rate-limited /
  upstream-error never record, empty-symbol short-circuits,
  record_measurement failure returns ``recorded=false`` without
  raising, batch iterates + sums articles, batch short-circuits on
  ceiling hit.
* 4 in ``test_news_shock_integration.py``: pins that NEWS_SHOCK
  fires only when BOTH sentiment_abs AND news_zscore clear their
  triggers (volume alone is chatter, sentiment alone is a single
  headline — neither is a shock), and that unpopulated feeders
  keep the classifier dormant.
* Plus 5 regression fixes across existing telemetry / classifier
  suites after the dataclass default shift.

**Live verification** (2026-05-02):

Batch feed of AAPL / NVDA / TSLA / MSFT succeeded end-to-end. All
4 symbols now have a baseline sample row in
``equity_telemetry_baselines``, daily counter progressed 3→6, and
the 2 s rate-limit lock held the spacing between calls (verified by
``fetched_at`` timestamps 2 s apart). Non-Saturday news day with
tagged-ticker flow will accumulate non-zero samples as expected.

**Still deferred**: ``news_sentiment_abs`` feeder. Free-tier Benzinga
does not reliably emit sentiment scores; populating this field
needs either a paid Benzinga tier or an LLM pass on the titles. Both
are viable when operator flips the key.

**63/63** tests green across the feeder + NEWS_SHOCK integration +
Benzinga client + liquidity-intelligence + iteration61-failure-mode
suites. Lint clean. Backend healthy.

### Tier-3 Brake Bypass + Smart Money Verification + Liquidity Intelligence Extension (May 2, 2026)

Four-part iteration shipping the next P0/P1 items on the roadmap.

**A. Commander Phase 2 brake — Tier 3 bypass**

`decide_brake` now accepts `tier3_unlocked: bool = False`. When True,
brake is pinned off with `reason="tier3_unlocked_full_authority"`
even on a clean disagreement — the adversarial engine gains full
directional authority at Tier 3 and a halve-on-disagreement
heuristic on top of that is double-counting. Disagreement still
records to the audit sub-doc so the admin panel sees continuity
rather than a cliff. `ml_paper_trader` reads `build_tier3_stats /
check_tier3_unlock` inline and threads the flag through. Any gate
read failure defaults to `tier3_unlocked=False` so the brake stays
live — fail-safe is to keep the guard on.

**B. Position Context design doc — `/app/memory/POSITION_CONTEXT_DESIGN.md`**

Complete contract for unblocking Terminal Phase T2 (`/top-actions`):
`PositionContext` dataclass shape, 6 data sources + fail-safe per
source, 5 correlation-dedup rules (exact overlap, sector saturation,
beta similarity, options delta overlap, crypto cluster), horizon
anchoring (intraday/swing/multi_day, one-per-candidate), ranking
formula, endpoint contract, 5 safety invariants, and 6 open
questions for operator sign-off. Implementation stays blocked until
those 6 answers land.

**C. Smart Money Verification — `decision_proof_chain` block**

* New `ProofEventType.SMART_MONEY_VERIFIED` in the enum (slots
  between `RISK_BUDGET_APPLIED` and `EXECUTION_ATTEMPTED`,
  chronologically correct in the IP lifecycle).
* New service `services/smart_money_verification.py` with three
  pure helpers + one Mongo-writing coordinator:
  - `compute_alignment(action, signal)` — pure matrix:
    LONG/bullish→confirms, LONG/bearish→contradicts, etc.
    Unknown action synonyms degrade to `no_data` (never raises).
  - `build_verification_payload(symbol, action, score)` — shapes a
    schema-stable payload (top 3 contributors only — keeps the
    `payload_hash` deterministic regardless of how long the
    contributor list gets upstream).
  - `verify_and_append(db, entity_id, symbol, action)` — fetches
    `compute_smart_money_score`, writes a `SMART_MONEY_VERIFIED`
    block via `AsyncMongoProofChainStore`, returns the payload.
    Two fail-safe branches: if score fetch fails, writes a
    `no_data` block anyway (chain records that verification was
    *attempted*); if the Mongo insert fails, logs and returns
    `None` so callers are never blocked by an audit-write outage.
* 14 tests pinning the alignment matrix (including the 3
  strategist-action synonyms and 2 commander synonyms), payload
  schema stability, happy-path block insertion, degradation under
  score-fetch error, Mongo-write-failure fail-safe, and `db=None`
  short-circuit.

**D. Liquidity Intelligence Extension — dollar-volume starvation**

* `MarketTelemetry` gains `dollar_volume` + `dollar_volume_baseline`
  fields (both default 0.0 so legacy callers are unaffected).
* `FailureModeConfig.dollar_volume_ratio_trigger = 0.30` — below
  30% of baseline trips the trap branch.
* `classify_failure_mode` now adds a dollar-volume starvation
  branch of `LIQUIDITY_TRAP`, independent of spread. Fires only
  when BOTH `dollar_volume_baseline > 0` AND `dollar_volume > 0`
  AND `dv_ratio < trigger` — double-guard against the zero-value
  footgun that would otherwise false-positive on fresh-deploy
  symbols with no history yet. Confidence scales with deficit
  depth.
* `services/equity_telemetry.py` extended end-to-end:
  `record_measurement(dollar_volume=…)` appends to the rolling
  buffer; `get_telemetry(current_dollar_volume=…)` computes the
  baseline and returns a populated telemetry object.
* 11 tests covering: legacy-caller safety, both-side zero guard,
  severe deficit trigger (confidence), boundary (ratio ==
  trigger → no fire), healthy dollar volume no-op, confidence
  scales with deficit depth, independent firing from spread,
  spread-only path still works (back-compat), custom-config
  override.

**Totals**: 61 new tests + 206/206 green across all adjacent
regression suites. Lint clean across all 9 modified / created
files. Backend restart succeeded (483 routes).

### Commander Shadow Phase 2 Pre-Tier-3 Size Brake (May 2, 2026)

Completes the Commander shadow lifecycle on the equity path. Phase 1
(logging only) has been accumulating `research_shadow_decisions` rows
with back-patched `tactical_score.shadow_was_right` since the earlier
session; the gate in `equity_shadow_promotion.py` flips
`brake_eligible=True` automatically once 50+ rows clear at ≥70% win
rate. This iteration wires the brake that actually *uses* that flag.

**1. Pure brake decision module** (`services/commander_phase2_brake.py`)

* `decide_brake(strategist_action, commander_decision, brake_eligible)`
  returns a `BrakeDecision` dataclass. Zero I/O, sub-millisecond, so the
  equity entry path pays no latency cost.
* `BRAKE_MULTIPLIER = 0.5` — hard-coded per the handoff spec. Not
  env-configurable: the rollout playbook calls for "halve or stop",
  not "dial a knob", and audit-stability across deploys matters more
  than tunability here.
* Safety invariant (pinned by `test_phase_1_never_brakes_even_on_disagreement`):
  `brake_eligible=False` → `brake_applied=False` **unconditionally**,
  even on a textbook Strategist/Commander disagreement. Phase 1 stays
  a pure logging lane until the promotion gate opens on its own.
* `disagreement` is recorded separately from `brake_applied` so Phase 1
  evidence collection still tracks "Commander would have disagreed"
  without touching size.
* Unknown strategist / commander action strings degrade to no-brake
  rather than raising (fail-safe). Telemetry logs `reason="unknown_*"`
  so a contract drift upstream surfaces in logs instead of silently
  misclassifying a typoed action as HOLD and suppressing the brake.

**2. Wire-up in `services/ml_paper_trader.maybe_paper_trade`**

Right after `half_kelly_position(...)` produces `position_usd`:
1. Build a commander signal dict from the strategist's direction +
   confidence + regime + raw indicators on `FeaturesSnapshot`.
2. Run `bull_agent` / `bear_agent` / `resolve_adversarial` inline
   (pure compute, microseconds).
3. Read `compute_equity_shadow_promotion_status(db)` to check
   `brake_eligible`.
4. Apply `decide_brake(...)`; if `brake_applied`, multiply
   `position_usd` by 0.5.
5. Persist the full brake audit (`commander_phase2_brake`) onto the
   `paper_trades` row so `/api/admin/commander-shadow/brake-activity`
   can count brake events without re-running the engine.

Any exception in this path — including a Mongo hiccup reading the
promotion gate or an adversarial pure-function crash — **never
blocks the trade**. Fail-safe degrades to Phase 1 (no brake).

**3. Admin observability endpoint**
`GET /api/admin/commander-shadow/brake-activity?hours=N` (owner-only,
capped at 168h window). Returns rolling counts:
`trades_evaluated / trades_braked / trades_disagreement_logged /
brake_rate_pct` plus the 10 most recent braked trades. Complements
the existing `/commander-shadow/promotion-status` — that one tells
you *whether* the gate has opened, this one tells you *how often*
the brake fires once it does.

**Tests** (`tests/test_commander_phase2_brake.py`): 23 cases,
0.13s total. Covers the Phase 1 safety invariant, Phase 2 brake on
all 7 disagreement combinations (including LONG/BUY/STRONG_BUY and
SHORT_OR_AVOID/SHORT_OR_REJECT/NO_TRADE synonyms), Phase 2 agreement
no-op (4 combinations), strategist=HOLD always no-ops regardless of
commander verdict, unknown-input fail-safe, and the `to_log` /
`apply_brake_to_position` helpers.

**Current live state** (verified via curl on the external preview):
Gate is closed — `brake_eligible=false`, `phase=phase_1_logging_only`,
1/50 scored rows. Brake evaluation wiring is live but the brake
multiplier stays at 1.0 until the gate unlocks automatically.


### Multi-action Integrity Mitigation + Slack Activation Notifier (May 1, 2026)

Extended the Integrity Mitigation self-defense layer from a single
`DEGRADE_TRADING` action to a three-action vocabulary plus a
best-effort Slack webhook ping on every activation.

**New actions**

* `BLOCK_NEW_BOTS` — while active, `POST /api/bots` and
  `PATCH /api/bots/{id}/toggle` (with `enabled=true`) return
  **HTTP 423 Locked** with `error_code: integrity_block_new_bots`.
  Existing running bots keep going; the gate only blocks new
  capital commitments and re-enables while the data is suspect.
  Toggling a bot *off* is always allowed (operators must be able
  to pause during an incident).
* `FREEZE_SIZING_OVERRIDES` — while active,
  `POST /api/admin/guard-shadow/policy/promote` and `/clear`
  return **HTTP 423** with
  `error_code: integrity_freeze_sizing_overrides`. Prevents
  operators from silently loosening per-patent enforcement flags
  mid-incident — a drift event is precisely when we DON'T want
  guard-rail flags mutated.
* `DEGRADE_TRADING` — unchanged from the prior session
  (multiplier clamp + strong-signal suppression on the crypto bot).

**Slack activation notifier**
(`services/integrity_mitigation_service._notify_slack_activation`)

* Fires a single card to `SLACK_WEBHOOK_URL` on every activation.
* Kill-switch: `INTEGRITY_MITIGATION_SLACK_ENABLED=false` silences
  it without touching the Mongo audit trail.
* **Anti-black-hole discipline**: a webhook outage NEVER blocks
  activation — the Mongo row is committed before the notify call,
  and the wrapper swallows notifier exceptions (pinned by
  `test_slack_notifier_never_blocks_activation`).

**Schema tightening**

* `AlertRuleUpsert` now validates `mitigation.action` against
  `SUPPORTED_ACTIONS` at create time — operators can no longer
  ship a rule with a typoed action that silently no-ops at
  evaluator time. Unknown actions respond 400 with
  `error_code: unsupported_mitigation_action` and the supported
  list.

**Summary endpoint**

`GET /api/admin/data-integrity/summary` → `mitigation` block now
carries `block_new_bots` and `freeze_sizing_overrides` booleans
alongside the existing `risk_multiplier` / `suppress_strong_signals`.
Frontend banner surfaces all four flags + per-item action type +
operator reason.

**Tests**: 10 pytest cases in `backend/tests/test_integrity_mitigation.py`
(4 original + 6 new: BLOCK_NEW_BOTS helper, FREEZE_SIZING_OVERRIDES
helper, 3-action summary rollup, Slack-never-blocks-activation,
Slack-silenced-by-flag, Slack-silenced-when-webhook-unset).
Testing agent added 7 API integration tests covering the full
HTTP gate flow (`iteration_163.json`: **17/17 green**, zero
critical/minor issues, zero action items). Lint clean across all
5 modified files.

### Integrity Mitigation — Self-Defense Layer Closed (May 1, 2026)

Final protection layer of the data-integrity loop: detect drift →
alert → **auto-activate mitigation → reduce risk / suppress strong
signals → auto-expire → audit trail**. The service file
(`backend/services/integrity_mitigation_service.py`) and evaluator
hook were already in place from the prior session; this iteration
closed the wiring on the five remaining endpoints.

**1. Crypto-bot sizing honours the effective multiplier**

`services/crypto_paper_trader.py::_process_one_symbol` now, after
the adversarial sizing but before the Patent-K/M/I guard, reads
`get_effective_integrity_risk_multiplier(db)` and
`should_suppress_strong_signals(db)`. If suppression is active and
the raw signal direction is STRONG_BUY/STRONG_SELL OR confidence
≥ 0.90, the trade is downgraded to HOLD with reason
`integrity_suppress_strong`. Otherwise the effective multiplier
clamps the notional. Lookup is wrapped so a mitigation-read
failure never blocks a trade (fail-open is safer than paralyzing
the fleet).

**2. Admin summary surfaces the mitigation state**

`GET /api/admin/data-integrity/summary` now returns a `mitigation`
block (active, active_count, risk_multiplier,
suppress_strong_signals, items[]) via
`summarize_integrity_mitigation_state`. Default (nothing active)
is the strict no-op shape: `{active:false, active_count:0,
risk_multiplier:1.0, suppress_strong_signals:false, items:[]}`.

**3. 5-minute TTL sweep scheduler**

New APScheduler job `integrity_mitigation_sweep` runs every 5
minutes, calling `expire_integrity_mitigations` +
`refresh_sync_cache`. This narrows the worst-case window between
a mitigation's TTL elapsing and the sync-side sizing cache
reflecting it from ≤15 min (alert evaluator cadence) to ≤5 min.

**4. Frontend amber banner**

`DataIntegrityPanel.jsx` renders an amber
`[data-testid=integrity-mitigation-banner]` when
`summary.mitigation.active === true`, showing risk multiplier,
suppress flag, active-rule count, and per-item source_rule_id +
expires_at + params. Header badge also flips to "Mitigation
active" (amber) instead of "Action needed" (orange) so operators
can distinguish "we auto-defended" from "we need you now".

**5. Pytest suite (4 tests + 7 API tests)**

`backend/tests/test_integrity_mitigation.py` pins the four
invariants: (1) multiplier defaults to 1.0 when nothing active,
(2) activate clamps + TTL flips to inactive (never deleted —
audit trail preserved), (3) `should_suppress_strong_signals`
requires the explicit opt-in flag (silence by default), (4)
multiplier clamps to [0.25, 1.0] and unsupported actions are a
no-op insert. Testing agent added
`test_integrity_mitigation_api.py` covering the full HTTP flow
(login → create rule with mitigation → evaluate-now → summary
reflects active → cleanup → TTL expire returns to defaults).

**Testing agent verdict** (`iteration_162.json`): 11/11 green
(4 unit + 7 API), zero critical/minor issues, zero action items.
Lint clean across all 5 modified files. Backend restart
succeeded without service regressions.

### War Room Hub — Unified Search (Option A) (Feb 28, 2026)

A single ticker entry at the top of the War Room hub now fans out to
the three per-symbol surfaces (Adversarial AI, Hypothesis,
Intelligence) in parallel via frontend `Promise.all`. Eliminates the
2–5 second delay that previously happened every time a user switched
tabs and re-typed the ticker.

**Why "Option A" (frontend prefetch) over a backend aggregator:**
The three endpoints don't share data sources (War Room hits AlphaVantage
+ Finnhub, Hypothesis scrapes news, Intelligence runs ML inference) so
a backend bundle would not save compute. Frontend `Promise.all` keeps
each panel independent — one slow upstream doesn't block the others
and partial failures degrade per-panel.

**Implementation:**

- `frontend/src/components/hubs/WarRoomHub.jsx` — added `runUnifiedSearch`
  that fires `/api/intelligence/war-room/{sym}`, `/api/hypothesis/{sym}?model=gpt-5.2`,
  and `/api/intelligence/{patterns,brief}/{sym}` independently. Each panel
  gets its own loading/error/data slice and renders as it resolves.
- `AIWarRoom.jsx`, `AIHypothesis.jsx`, `AIIntelligence.jsx` — accept an
  optional `prefetched` prop. When supplied (`isControlled = true`) the
  internal search bar / model selector is hidden and the component
  renders the prefetched bundle. Standalone usages (DashboardView,
  ResearchHub) pass nothing → fall back to local fetch as before.
- AIIntelligence has internal patterns/brief sub-tabs, so its prefetched
  bundle uses `results: { patterns, brief }` keyed by sub-tab.
- Unified search bar visible only on the per-symbol tabs (adversarial,
  hypothesis, intelligence) — hidden on Predictions/Signals.
- Recent ticker chips and the existing `risedualai-warroom` deep-link
  CustomEvent now route through the unified search.

**Verified by testing agent (iteration_160):** 95% frontend pass.
Unified search correctly fans out, individual search bars correctly
hidden when controlled, tab switching reuses prefetched data, empty
states display the right messaging. The only flagged item is unrelated
React duplicate-key warnings in order book components (out of scope).



### ETL Framework for Periodic External-Source Ingestion (Feb 27, 2026)

Generic scaffolding for the "slow-data cache" pattern: pull from
external APIs on a cron cadence, normalize, upsert to Mongo, let
Mongo's TTL daemon handle retention. Adding a new source is now
~30 lines instead of ~150.

**1. Base class** (`services/etl_registry.py::BaseETLJob`)

Subclasses declare four class attributes and implement
``fetch()``. The framework handles: unique-key upsert (composite
index), TTL retention (180-day default, per-subclass
override), concurrent-run guard (in-process `asyncio.Lock` per
job), per-row fault tolerance (malformed row counts as
`failed`, rest of batch continues), error capture (no
exception propagates out of `run()`), and audit logging.

The key retention design choice: `first_seen_at` is pinned via
`$setOnInsert` and **never updated on re-fetches** — it's the
TTL anchor so a row always expires 6 months after first ingest,
regardless of how often it's re-confirmed by the source.
`last_fetched_at` updates every run for operator visibility
("when did we last see this row in the source?").

**2. Registry + scheduler wiring**

* `@register_etl_job` class decorator — auto-register subclasses.
  Uniqueness-checked on `source_name`.
* `services/etl_registry::all_jobs()` + `get_job(name)` —
  lookup helpers.
* `server.py` startup iterates `all_jobs()` and registers each
  with APScheduler using the subclass's declared `cadence` dict
  (any APScheduler cron trigger kwargs). Disabled jobs skipped.

**3. Audit log**

* Every `run()` invocation writes one row to `etl_run_log`
  (`run_id`, `trigger`, `status`, counts, duration, error).
* TTL index: audit rows expire at 90 days (3 months earlier
  than the cache rows they describe — losing audit history
  before the cache itself is gone is fine).

**4. Admin endpoints** (`routes/admin_etl.py`, all owner-gated)

* `GET /api/admin/etl/jobs` — list every registered job + last
  run summary.
* `GET /api/admin/etl/jobs/{source_name}` — detail + recent
  history (up to 20 runs).
* `POST /api/admin/etl/jobs/{source_name}/run` — manual
  trigger for incident response. The concurrent-run guard
  prevents double-fires if a scheduled run is already in
  flight; returns `status=already_running` cleanly.

**5. Index ensure on startup**

`route_registry.py` adds two batches:
* `ensure_audit_indexes` for `etl_run_log`.
* `job.ensure_indexes()` for every registered subclass —
  creates the composite unique index + the TTL index on
  `first_seen_at`.

**Tests**: 18 new pytest cases in `test_etl_registry.py`
covering the full contract: subclass rejection (missing
source_name / unique_key / cadence / fetch impl), registry
uniqueness + lookup, `ensure_indexes` (unique + TTL with
correct `expireAfterSeconds`), happy-path with
`first_seen_at` immutability + `last_fetched_at` updates,
dedup via unique key, `transform()` hook, fetch-raising
becomes `failed`, malformed-row fault tolerance, Mongo
upsert-raising counted as failed, concurrent-run guard,
audit-row write, `get_last_run` / `get_run_history` ordering
and empty-state.

**230/230** sync + framework + tracer + existing-regression
test suite green (was 212 before this change). Lint clean
across all 3 new files. Backend boot verified, admin
endpoint returns `count: 0` as expected (framework ships with
zero concrete subclasses — operator adds them next).

**No concrete subclasses shipped yet — the framework is the
deliverable.** First concrete candidate when operator is
ready: a Quiver congressional-trades weekly ETL, replacing
the current in-process 6h sliding cache. Estimated ~30
lines, ~15 min.

### Deployment Readiness Audit — CLEARED TO SHIP (Feb 27, 2026)

Final pre-deploy health check. Applied 7 security patches earlier
in this session (axios, react-router, defusedxml swap in 3 files,
chunked-upload path-traversal regex + realpath check) and one
additional tz-safety fix in `services/research_router.py:113`
(`cached_at = ensure_utc(cached_at)` before TTL comparison — same
hardening pattern as `mongo_chroma_sync_metrics`, closes a bug
that was silently forcing Tavily re-fetches 4× per crypto tick
on ETH/SOL).

**Staging validation (2026-04-30, post-patch)**:
* Forced stale-cache fetch (cache entry ts > TTL) → `ensure_utc`
  path executed, no exception, expected refresh fired
  downstream. Code path proven end-to-end.
* Watched `backend.err.log` for ≥ 1 full TTL window post-deploy
  → silent. Was 4× per crypto tick pre-fix; now zero.
  Production behavior matches intent.

**Final metrics:**

| Check | Result |
|---|---|
| Pytest sync+tracer+rebuild+tz suite | **212/212 passing** (was 209) |
| Bandit HIGH+MEDIUM (prod code) | **0** (was 4) |
| npm audit HIGH/CRITICAL (runtime) | **0** (was 3) |
| Backend post-restart err logs | silent on known bugs |
| Supervisor services | 4/4 RUNNING |
| Required env vars | 18/18 present |
| Hardcoded URLs in JS | 0 (broker doc links excluded) |

**3 accepted operational trade-offs** — not blockers, tracked
formally in `/app/memory/OPS_BACKLOG.md`:

* **OPS-001** — `/tmp/uploads/` chunked-upload loss on pod
  restart. Admin-only, sub-30s upload window, graceful client
  failure. Revisit if upload size > 500 MB.
* **OPS-002** — Reuters + AP RSS DNS blocked in preview pod.
  Multi-source design degrades gracefully; world-events still
  produces output from WSJ/Bloomberg/FT/SEC sources. Revisit
  if production deploy has the same block.
* **OPS-003** — ML deps container size (~500-800 MB over
  baseline). Inference-only, zero SLO impact, RAM
  steady-state ~700 MB. Revisit if deploy > 5 min or
  cold-start > 30s.

### One-Click Wipe-and-Rebuild Recovery + Card Polish (Feb 27, 2026)

Closes the operator gap on "what do we do if the toxic-spike bug
recurs?" The previous answer was "drop into a Python REPL or
write curl commands." The new answer is one button on the drift
card with two confirm modes.

**1. Backend: destructive `wipe=true` mode on the rebuild endpoint**

* `services/market_memory_service.reset_collection()` — new
  helper. Drops the entire ChromaDB collection via
  ``delete_collection`` (single HNSW index drop, not a per-row
  delete loop) and recreates it with identical embedding config.
  Returns the pre-wipe count for the operator audit trail.
* `POST /api/accuracy/memory/rebuild-from-mongo?wipe=true` —
  destructive flag. When set, calls ``reset_collection`` BEFORE
  iterating predictions (order matters — wiping after would
  discard the freshly-saved rows). Default `wipe=false` is
  unchanged (upsert-only, backwards-compatible with any existing
  automation).
* Owner-only auth gate runs **before** the wipe; a 403 leaves
  the collection intact. DB-unavailable check also runs before
  the wipe — we never wipe Chroma when Mongo is unreachable
  (would leave the system in an unrecoverable state).
* Structured `WARNING`-level log on every wipe with
  user_email + role + days + limit, so accidental clicks are
  forensically traceable.

**2. Frontend: rebuild button + confirm dialog
(`MemoryDriftCard.jsx`)**

* Header now has a `Rebuild ↺` button next to `Refresh`. Visual
  weight increases when `recommendation === "rebuild"` (red
  background) so the action is unmissable during an incident.
* Click opens a card-local modal offering two paths:
  * **Refresh (upsert) — safe, default**: backfills missing
    rows, preserves training over-supply.
  * **Wipe + rebuild — destructive**: gated behind a native
    `window.confirm()` second-tier prompt explaining that
    training over-supply will be lost.
* Result toast renders inline: green "rebuilt N · skipped M ·
  wiped K" on success, red error message with dismiss control
  on failure. Auto-reloads the drift snapshot so the operator
  sees the new state without a manual refresh.

**3. Card polish (this session, all visual)**

* `DriftNarrative` — plain-English translation of the raw
  `drift` number. Three branches (positive / negative /
  perfectly aligned) with copy that maps to the operator's
  mental model. Negative drift now reads as "Chroma has N
  more episodes than Mongo's verified set — Expected. The
  detector clamps negative drift to 0% because over-supply
  isn't a corruption signal." instead of just showing -926.
* `DriftSparkline` warmup states — three phases (0 samples,
  1 sample, < 24 samples) each with explanatory copy that
  auto-disappears as the watcher accumulates data. Operators
  no longer wonder if a thin sparkline means broken watcher.
* "Why this watcher exists" — collapsible `<details>` section
  at the bottom with the 2026-04-21 toxic-spike retroactive
  case study (`mongo:412 / chroma:12`). Institutional memory
  inside the tool.

**Tests**: 6 new pytest cases in `test_memory_rebuild_wipe.py`
covering: default behaviour unchanged, wipe path calls reset
exactly once before iteration, wipe blocks non-admin (403),
wipe respects DB-unavailable guard, `reset_collection` returns
pre-wipe count, `reset_collection` uninitialized no-op.
**209/209** green across the full sync + rebuild + tracer
test layer (was 122 last commit). Lint clean across all 5
modified files.

### Self-Hosted Langfuse Observability — Council v2 LLM Tracing (Feb 27, 2026)

The first observability surface for the multi-LLM consensus
panel. Everything self-hosted (Docker Compose alongside the
backend), all data stays inside the operator's trust boundary
per the IP-defensible / Patent J discipline. No SaaS account
required.

**1. Defensive tracer module** (`services/langfuse_tracer.py`)

* Lazy-singleton client — constructed only on first call, never
  at import time.
* Master kill-switch: `LANGFUSE_ENABLED=false` (or any of
  the three keys missing) → permanent no-op. Bot loop
  unaffected.
* Circuit breaker: SDK init failure (network unreachable, bad
  keys, server down) caches the failure for 5 minutes. Prevents
  reconnect storms against a dead langfuse-server.
* Sync + async context-manager helpers: `traced_span(...)` /
  `atraced_span(...)` yield `None` when disabled. Caller code
  reads as if tracing were always-on; the null branch is
  invisible at the call site.
* `span_update(span, ...)` accepts `None` and swallows SDK
  exceptions — same discipline as the proof-chain emit on close.
* **Anti-black-hole guarantee**: caller exceptions inside
  the with-block are NEVER swallowed by the tracer. Bot bugs
  still propagate to the bot loop's exception handler.
  Pinned by 2 regression tests
  (`test_traced_span_does_not_swallow_caller_exception` +
  async variant).

**2. Council v2 instrumentation**
(`services/research_shadow_engines.py`)

* `_run_council_llm()` — wraps the parallel `asyncio.gather`
  with a parent span (`council_llm_panel`, `as_type=span`).
  Captures the symbol, asset_type, active signal context as
  input; the weighted-vote winner + per-model votes + total cost
  + ok-count / failed-count as output.
* `_run_single_council_model()` — wraps each model call as a
  child generation (`council_llm_{provider}`,
  `as_type=generation`). Captures the prompt, raw completion,
  parsed action/confidence/reason, and the
  pre-computed `_LLM_COUNCIL_COST_USD[provider]` via Langfuse's
  `cost_details` (Universal Key pricing isn't auto-discoverable
  by the SDK — we override explicitly).
* Failure mode visibility: when a single model errors out, the
  span is tagged `level=ERROR` with the exception type +
  truncated message so an operator can spot a flaky provider
  in the trace timeline.

**3. Self-hosted infrastructure**

* `/app/docker-compose.langfuse.yml` — runs `langfuse/langfuse:2`
  + `postgres:15-alpine` on the operator's machine. Uses
  port 3090 (host) → 3000 (container) to avoid conflicts.
  TELEMETRY_ENABLED=false; data never leaves the operator's
  network. Healthcheck on Postgres so the langfuse-server
  doesn't race the migration.
* `/app/LANGFUSE_SETUP.md` — end-to-end runbook: start →
  signup → mint keys → wire `.env` → smoke test. Includes
  ops sections (disabling without removing keys, server
  outage behaviour, cost budget, retention math).

**4. Env contract** (all optional, all leave bot in no-op
state when missing):

| Var | Purpose |
|---|---|
| `LANGFUSE_ENABLED` | Master kill switch (default `true`) |
| `LANGFUSE_HOST` | e.g. `http://localhost:3090` |
| `LANGFUSE_PUBLIC_KEY` | From langfuse-server UI signup |
| `LANGFUSE_SECRET_KEY` | From langfuse-server UI signup |
| `LANGFUSE_DEBUG` | SDK-level log verbosity (default `false`) |

**5. Cost & risk profile**

* Tracing adds zero LLM cost — Langfuse stores the trace, the
  LLM cost is the LLM cost. The existing
  `SHADOW_COST_CEILING_USD_PER_DAY=5` ceiling is unchanged.
* Disk usage: ~2 KB per Council round → ~125 MB/year at the
  steady-state crypto-fleet cadence. No retention pruning needed
  for the foreseeable future.
* Network: 5s hard timeout on the SDK init. Failed connect
  triggers the 5-minute circuit breaker — bot keeps running
  with zero overhead during the cooldown.

**Tests**: 16 new pytest cases in `test_langfuse_tracer.py`
covering: env-disabled / missing-key / kill-switch no-ops,
defensive `span_update(None)` and `flush()`, anti-black-hole
exception propagation, circuit-breaker caches the init failure,
singleton caches the success, SDK kwargs flow through correctly,
and end-to-end shape preservation of `_run_council_llm()` under
disabled tracing. Plus a no-op verification of the tracer pulled
in by the live preview process.

**Tests + lint**: 122/122 green across
`test_research_shadow.py + test_langfuse_tracer.py + the 4
sync/CI guard suites`. Lint clean across all 3 modified +
created Python files.

**What this is NOT** (set expectations):

* Not a security tool. Use Semgrep / Bandit for SAST.
* Not an APM. Use Sentry for unhandled exceptions.
* Not the audit chain. Patent J's `decision_proof_chain`
  remains the legal-grade tamper-evident record. Langfuse
  stores the same reasoning in plaintext for *operator
  debugging* — complementary, not redundant.

**To activate** (operator action required, ~10 min):

1. `docker compose -f docker-compose.langfuse.yml up -d`
2. Browse to `http://localhost:3090`, sign up, mint API keys.
3. Paste `LANGFUSE_HOST` + `LANGFUSE_PUBLIC_KEY` +
   `LANGFUSE_SECRET_KEY` into `/app/backend/.env`.
4. `sudo supervisorctl restart backend` — log will show
   `[langfuse] connected: http://localhost:3090`.

Until then the instrumentation is a complete no-op.

### Drift Sparkline + Email Channel for Drift Alerts (Feb 27, 2026)

Two operator-grade follow-ups on the drift detector — closes the
"operator must poll the admin panel to see drift trends" gap and
the "Mongo queue alerts can't be missed but only if you're
already in admin" gap.

**1. Drift trend sparkline (24h)**

* `services/mongo_chroma_sync_metrics.py` extended:
  * `record_drift_sample()` — persists a tiny snapshot per
    5-min watcher tick to `mongo_chroma_drift_history`. Tz-aware
    UTC datetime on write; ``ensure_utc(...)`` re-tags on read so
    the round-tripped naive datetime can't silently shift the
    sparkline by the operator's local-time offset.
  * `get_drift_history(hours)` — returns ascending series with
    ISO-formatted UTC timestamps (explicit `+00:00` offset
    guaranteed via `ensure_utc`).
  * `ensure_history_indexes()` — TTL index (7 days) so the
    collection stays bounded ~2k docs steady-state at a 5-min
    cadence. Wired into `route_registry.py`'s startup batch.
* `services/drift_alert_watcher.py::check_and_alert()` calls
  `record_drift_sample()` on every tick, even on the quiet
  path. Failure isolated — a Mongo write blip can't crash the
  scheduler tick or block the alert pipeline.
* New endpoint `GET /api/admin/memory/drift/history?hours=24`
  (owner-only, max 168h tied to the TTL retention).
* `MemoryDriftCard.jsx` renders an inline-SVG sparkline below
  the metric tiles. Color tier matches the recommendation
  classifier (emerald < 1%, amber < 10%, red ≥ 10%); dashed
  red reference line at the rebuild threshold; trend delta
  pill (`+1.20pt` / `flat` / `-0.50pt`) so the operator can
  see direction at a glance. Renders nothing when < 2 samples
  exist (fresh deploy / scheduler hasn't ticked) — no skeleton
  placeholder eating vertical space.

**2. Email channel for actionable drift alerts**

* `services/drift_alert_watcher.py::_dispatch_email()`:
  * Routes only the actionable types
    (`memory_drift_rebuild_recommended` + `memory_drift_recovered`)
    through `email_service._routed_send`. Jump alerts stay
    Mongo-only — they can fire repeatedly per day per
    `(prev→curr)` bucket and would clog the operator inbox.
  * Suppressed on the dedup path: when `ai_core_alerts.emit()`
    reports `deduped=True`, the email is skipped — the
    operator already received it earlier today.
  * Mute switch: `DRIFT_ALERT_EMAIL_ENABLED=false` (default
    `true`). Best-effort wrap so an email provider outage
    can't poison the scheduler.
  * `OWNER_EMAIL` env (existing) is the recipient. Subject
    line includes `[RISEDUAL]` prefix; body renders the alert
    metadata as a structured table for forensic value.

**Mongo datetime safety**: audited end-to-end against the two
existing CI guards (`test_no_unguarded_mongo_datetime_math.py`,
`test_no_brittle_slice_on_mongo_dates.py`) — both green. The
new code uses `datetime.now(timezone.utc) - timedelta(...)` for
filter cutoffs (freshly-created tz-aware datetime, never a
Mongo round-tripped one) and explicitly wraps every Mongo-read
`ts` field in `ensure_utc(...)` before serializing. No `[:10]`
slicing on Mongo dates anywhere.

**Live verified**: 3 `check_and_alert()` invocations against
the live preview DB persisted 3 rows in `mongo_chroma_drift_history`
with tz-aware ISO strings (`2026-04-30T12:13:46+00:00`),
`drift_pct=0.0`, `recommendation=ok`. No alerts fired (quiet
path), no email dispatched.

**Tests**: 11 new pytest cases — 7 in
`test_drift_alert_watcher.py` (rebuild→email, jump→no-email,
flag-off→no-email, deduped→no-email, sample-on-every-tick,
record-failure-doesn't-block, recovery dispatch path) + 4 in
`test_memory_drift_detector.py` (record_drift_sample shape,
mongo-failure swallowed, get_drift_history tz re-tag,
endpoint payload contract, db-unavailable graceful). **120/120**
green across the full sync test layer (was 109 before this
change). Lint clean across all 4 modified backend files +
the frontend card.

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

