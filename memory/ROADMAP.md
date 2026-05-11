# RISEDUAL AI — Roadmap

Prioritized backlog. P0 = blocking · P1 = next sprint · P2 = future · P3 = vision.

---

## 🟢 Ready to ship (queued in DEPLOYMENT_NOTES.md)

See `/app/memory/DEPLOYMENT_NOTES.md` → `🟡 Queued for next deploy` for the
current live deploy queue.

---

## P0 — Imminent

- **Wire the crypto bots to the new isolated paper line.** ✅ DONE
  (Apr 25). Service + route + collection + scheduler + Strategist/
  Auditor signal layer + 12h max-hold closer all live. See
  PRD §4 "Crypto Bot v2".

- **Shadow-mode Web Research (Tavily + LLM stance).** ✅ DONE
  (Apr 26). New services: `web_research_service.py` (Tavily HTTP +
  EMERGENT_LLM_KEY stance classifier with strict JSON parser) and
  `research_router.py` (cost-aware gate: high-conviction OR narrative
  regime; 10-min Mongo cache; `CRYPTO_SHADOW_RESEARCH_DISABLED=1`
  ops kill switch). Hooked into `crypto_paper_trader.run_crypto_symbol`
  AFTER the strategist/auditor produced a final LONG/SHORT signal —
  verdict is persisted on both `crypto_signal_audit_log.web_research_shadow_verdict`
  AND `crypto_paper_trades.web_research_shadow_verdict` for later
  expectancy correlation. **Verdict is logged only — never alters
  direction or confidence in the live path.** 41 new pytest cases
  cover gate / cache / classifier / failure-mode / shadow-isolation.

- **Alpaca live-key plumbing.** User has Alpaca approval; waiting on key
  from email. All infrastructure is ready:
  - `.env` slots present (empty): `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
  - `ALPACA_BASE_URL` pinned to `paper-api.alpaca.markets`
  - `services/ml_alpaca_broker.py` adapter complete
  - Safety gates: `_MIN_LIVE_CONFIDENCE=0.50`, `_LIVE_OPT_IN` requires
    explicit `RISEDUAL_LIVE_EXECUTION=1` env var (keep unset during the
    30-day paper window)
  - UI: `BrokerConnect.jsx` + `AlpacaOAuthDemo.jsx`
  - Bot executor (`_execute_bot_trade`) already wired
  - Action when key arrives: paste into `.env`, restart backend, verify
    via Broker Connect health check.

---

## P1 — Next sprint

- **30-day paper-trading accumulation → ML Tier 3 unlock.** Currently at
  4/4 wins logged (2026-04-20: BTDR, KEY, GROY×2 — see
  `AI_PREDICTION_WINS.md`). Need 100/500/1000 labeled predictions for
  Tier 1/2/3 gates. Automated labeler is running hourly.

---

## ⏸ Parked — waiting on user action

- ~~**Wire Alpaca crypto LIVE execution.**~~ **DROPPED 2026-02-08
  per user.** ("Alpaca can get crossed off as well. Doesn't seem
  it's happening.") Crypto **paper** trading remains live via the
  isolated `crypto_paper_trader` + `crypto_quotes` lane. Kraken
  adapter still sits coded but dormant in `broker_service.py:986`
  as a possible future revival vector.

- **Drop `_CANONICAL_OWNER_PASSWORD` override** in
  `backend/routes/auth.py`. *(2026-02-08: user chose to rotate the
  password via UI first and verify login works before the override
  is removed. Do not drop until user confirms the new password has
  been tested post-rotation.)*

- **Flip `ML_ADAPTATION_SHADOW_MODE` → live.** *(2026-02-08: parked
  for ~2 weeks of shadow observation. Calibration endpoint
  currently reports 0 observations. Revisit when
  `/api/admin/adaptations/calibration` returns a stable p25
  recommendation with ≥20 observations.)*

  Flip procedure when ready:
  1. `GET /api/admin/adaptations/calibration?window_days=30` → read
     `recommendation.suggested_effect_size`.
  2. Set `ML_AUTO_REVERT_EFFECT_SIZE=<p25>` in `backend/.env`.
  3. Set `ML_ADAPTATION_AUTO_REVERT_ENABLED=true` in `backend/.env`.
  4. `sudo supervisorctl restart backend`.
  5. Watch the admin `ModelAdaptationsPanel` for the first live
     soften row (no longer tagged `shadow_`).

---

## P2 — Future

- **Kraken US Equities — second-broker stock routing** *(doc-only, no code)*

  Kraken opened US stock + ETF trading in early 2026 alongside its
  crypto book. That makes Kraken a candidate for a **unified
  crypto+equity broker** in RISEDUAL, sitting next to Alpaca.

  **Scope parked for now** — this entry is documentation only.
  No adapter, routes, or .env slots have been added yet. Do NOT
  start implementation without an explicit go-ahead.

  When we green-light this, the intended shape is:

  *Transport*
  - Kraken Pro REST (`api.kraken.com`) — same base URL as the
    existing crypto integration, new `/equities/*` route family.
  - Reuse the existing Kraken HMAC-SHA512 signing path in
    `services/kraken_*` (auth is shared across asset classes on
    Kraken Pro).
  - Sandbox/test creds already held in repo `.env` slots
    (`KRAKEN_API_KEY` / `KRAKEN_API_SECRET`) — user confirmed these
    can be used as-is during integration.

  *Rollout order* (when scheduled)
  1. **Phase 0 — Market data only.** Wire Kraken's equity quote /
     candle endpoints into the existing price provider pool as a
     tertiary source. Shadow-compare against Alpaca to validate
     symbology mapping (CUSIP / Kraken ticker / our canonical
     symbol). No orders placed.
  2. **Phase 1 — Paper trading / shadow layer.** Route equity
     paper fills through the Kraken adapter in a `shadow_` flag
     so we can measure fill latency, spreads, and reject rates vs
     Alpaca without touching the live order path.
  3. **Phase 2 — Live equity orders (opt-in).** Add an
     `EQUITY_BROKER_ROUTING` env var (values: `alpaca` /
     `kraken` / `best_venue`) and a hard-gated `RISEDUAL_LIVE_EXECUTION=1`
     override, mirroring the Alpaca live rollout pattern.
  4. **Phase 3 (maybe) — Equity options / ETFs.** Track Kraken's
     option contract availability before committing — not on the
     current critical path.

  *Safety invariants* (applied when we schedule the work)
  - Kraken equity integration MUST NOT regress the existing
    Kraken crypto bot. Separate service modules
    (`kraken_equity_broker.py`) so crypto adapters stay untouched.
  - Broker-ambiguity prevention: every order request must carry
    an explicit `asset_class: "equity" | "crypto"` field — no
    inferring from symbol shape.
  - Reuse `integrity_mitigation_service` ceiling + `confidence_gate`
    across both asset classes; the Sovereign AI layer is
    broker-agnostic by design.

  *Owner-tracked* — re-open this entry when there's capacity.
  Status: **Phase 0 SHIPPED 2026-05-03** (infrastructure complete,
  default OFF, opt-in via ``KRAKEN_SHADOW_ENABLED=1``).

  **Geo-block discovered on activation (2026-05-03)**: when the env
  flag was flipped on for a live verification run, the manual tick
  returned ``rows_written: 0 / no_listed_xstocks``. Probing
  Kraken's public REST API directly confirmed:

  * Pod egress IP is **US (Iowa)**.
  * ``GET /0/public/AssetPairs`` returns 1528 pairs but **0 rows
    with ``aclass_base == "tokenized_asset"``** from this region.
  * Direct ``Ticker?pair=AAPL*`` probes all return
    ``EQuery:Unknown asset pair`` regardless of pair-code form.

  **Root cause**: Kraken xStocks are regulatory-gated to **non-US
  jurisdictions** (MiCA / non-US frameworks). The earlier
  integration playbook framing (Alpaca custody partnership,
  one-to-one backing, etc.) applies only to non-US users; the
  same Kraken account sees no tokenized_asset rows when the
  request originates from a US IP.

  **What's working anyway**: the shadow service code is correct —
  symbol resolution gracefully writes ``not_listed`` sentinels,
  the scheduler tick short-circuits with a structured summary,
  zero rows persist, no orders or auth happen. The
  ``GET /api/admin/kraken-shadow/today`` endpoint, the burn-in
  chip, the slack alert path, and the divergence math are all
  exercised by the 18 unit tests and ready to flip on in
  *whichever* environment has non-US egress.

  **Re-activation path** (when ready):
  1. Either deploy to a Kubernetes cluster with non-US egress,
     OR plumb an outbound proxy (e.g. a small VPN sidecar or a
     CDN-fronted proxy in EU/UK) for ``api.kraken.com`` only.
     The shadow service uses a single base URL — surgical proxy
     scope is easy.
  2. Flip ``KRAKEN_SHADOW_ENABLED=1`` again. First tick discovers
     pair metadata and starts populating ``kraken_equity_shadow_compare``.
  3. Once ~24h of data is in, the burn-in chip's traffic-light
     status will start showing real divergence trends.

  Phase 1 — **live equity orders** (skip the paper/shadow routing
  step that the original entry described — Kraken doesn't offer
  paper trading; paper stays on Alpaca). Phase 1 needs the same
  non-US egress gate that blocks Phase 0 data today.
  Phase 2 — broker-routing env var
  ``EQUITY_BROKER_ROUTING ∈ {alpaca, kraken, best_venue}`` and the
  hard ``RISEDUAL_LIVE_EXECUTION=1`` opt-in.

- **Adversarial Core: Phase progression + per-regime weight tuning.**

  Status: **Bull / Bear / Commander layer built and shadow-gated**
  (Apr 27). Both gates currently closed
  (`CRYPTO_ADVERSARIAL_ENABLED` unset + ML Tier 3 locked) so the
  layer is silent. See PRD §8 Apr 27 entry.

  Remaining work, gated on the right data showing up:

  *Trigger 1 — Activate shadow logging:*
    - When ML Tier 3 unlocks (currently 5 reasons short).
    - Then set `CRYPTO_ADVERSARIAL_ENABLED=1` in `.env`.
    - Both gates open → `crypto_adversarial_decision_log` starts
      filling with Bull/Bear/Commander decisions on every fill,
      `decision_id` attached to each trade row, outcomes patched
      on close. Phase stays `shadow` — no behaviour change.

  *Trigger 2 — Promote to `risk_only`:*
    - 50–100 logged decisions accumulated.
    - Bull's `final_result_r` distribution shows positive expectancy
      when Bull "won" the resolver vs neutral when Bear won.
    - Then set `CRYPTO_ADVERSARIAL_PHASE=risk_only` in `.env`.
    - Caller scales position size by `risk_multiplier`. Direction
      unchanged.

  *Trigger 3 — Promote to `veto`:*
    - Decisions where Commander said NO_TRADE show better avg_r
      than the trades that actually fired (i.e. it's correctly
      filtering the bottom of the distribution).
    - Set `CRYPTO_ADVERSARIAL_PHASE=veto`.

  *Trigger 4 — Promote to `full` (full Commander control):*
    - Last step. Earned, not granted. Same statistical bar as
      Tier 3 itself.

  *Per-regime weight tuning (the original user proposal — saved
  verbatim from 2026-04-26):*

  ```python
  # USER'S ORIGINAL PROPOSAL — DO NOT use these numbers literally.
  # Empirically derive after 100+ logged adversarial decisions.
  if regime == "trending":   signal_w, narrative_w = 0.7, 0.3
  elif regime == "parabolic": signal_w, narrative_w = 0.4, 0.6
  elif regime == "uncertain": signal_w, narrative_w = 0.5, 0.5
  ```

  Once `crypto_adversarial_decision_log` has 100+ rows with
  outcomes, group by `regime` and compute the lift per bucket.
  The empirical values become the starting point for tuning
  `EDGE_GAP_THRESHOLD` per regime — currently a single global
  constant in `adversarial_core.py`.

  **Reference files:**
  - `services/adversarial_core.py` (pure-function Bull/Bear/Commander)
  - `services/adversarial_logger.py` (Mongo glue + outcome updater)
  - `services/crypto_paper_trader.py:run_crypto_symbol` (hook site)
  - `services/crypto_closer.py:run_crypto_closer` (outcome patch)
  - `tests/test_adversarial_core.py` + `tests/test_adversarial_logger.py`

- ~~**`/api/crypto/sltp-expectancy` analytics endpoint**~~
  ✅ DONE 2026-02-08. Read-only at `GET /api/crypto/sltp-expectancy`
  (admin only). Surfaces expectancy + close-reason mix +
  tighter-bracket what-ifs. 17 unit tests. See CHANGELOG (j).

  **Live finding on first run (98 closed trades):** expectancy
  = -0.06R, win_rate = 56.1%. Zero `take_profit` close reasons —
  80/98 trades exit via `hold_window_expired`. Headline:
  "Tightening SL to 30% of current would lift expectancy from
  -0.06R to +0.14R." Surfaced for human review; no auto-tune.

- ~~**Paper trader duplicate-insert race**~~ ✅ DONE 2026-02-08.
  Mongo unique partial index on
  `(ticker, direction, prediction_id, time_bucket)` enforced
  at the DB layer. `time_bucket = floor(opened_at_unix / 60)`.
  Insert path catches `DuplicateKeyError` and returns the
  existing trade_id. The 12-second AAPL twin scenario is now
  physically impossible. 6 unit tests. See CHANGELOG (j).

- ~~**Backtest/Live data labeling (Option B)**~~ ✅ DONE 2026-02-08.
  `services/data_source_labeler.py` annotates `data_source:
  "live" | "backtest"` at API response time based on
  `PUBLIC_DATA_FLOOR_DATE` (default `2026-04-23`). Wired into
  `/api/ml/paper-trades` and `/api/accuracy/history`. UI badge
  in `MLPaperPnL.jsx`. 17 unit tests. See CHANGELOG (j).

- **QuantConnect ↔ QuiverQuant bridge** (user's QC algo pending).
  Replaces flaky Quiver REST with QC Cloud pipeline for Lobbying +
  Insider Trading datasets.

- ~~**Data consolidation: Polygon.io adapter A/B vs Finnhub**~~
  ✅ DONE 2026-02-08. Polygon adapter wired into
  `market_data_pool` with `source: "polygon"` tag. Auto-registers
  when `POLYGON_API_KEY` is set; default priority 4 (overridable
  via `MARKET_DATA_POLYGON_PRIORITY`). Set priority=1 to A/B
  Polygon as primary. 12 unit tests. See CHANGELOG (j).

  *Future consolidation candidate:* **Financial Modeling Prep**
  for fundamentals + insider data — could retire the QuiverQuant
  direct dep if quality acceptable.

- ~~**Pro Max tier UI wiring.**~~ ✅ DONE (verified 2026-02-08).
  Backend accepts `pro_max` + `pro_max_annual`, frontend
  `SubscriptionPricing.jsx` posts `selectedTier.key` correctly,
  Stripe live checkout URLs return for both. The earlier
  `plan=monthly` issue was already resolved in a prior pass.

---

## P3 — Vision / strategic projects

### 🧠 Meta-Classifier Challenger Layer (designed 2026-05-12, parked)

**Status**: design-complete (5-iteration arc with operator), implementation deferred. NOT to start until current ADL organics window completes AND operator green-lights.

**What it is**: a second-tier adversarial layer that complements the existing prompt-driven Bull/Bear/Commander cores. Where Bull/Bear opine on *direction* under prompt, the new challengers opine on `P(majority is wrong | features)` as **calibrated binary meta-classifiers** trained against `verified_24h.correct` ground truth.

**Core design** (locked through 5 iterations of operator critique):
- 4 proposers + 3 challengers. Proposers multi-class (LONG/SHORT/HOLD/NO_TRADE/UNKNOWN); challengers binary meta (`majority_wrong`: 0/1).
- All cores calibrated via `CalibratedClassifierCV(method="isotonic")` — symmetric calibration is non-negotiable.
- Challengers use `class_weight="balanced"` (HistGradientBoostingClassifier replaces GBC so the API works). Imbalance is the silent killer.
- Meta-target: `(majority_vote != y) & (avg_confs > 0.7) & (~verified_correct.astype(bool))` — **stationary** because locked to verified ground truth, not live proposer state.
- Veto = abstain (`NO_TRADE`). No alternative-class override — challengers are meta-classifiers, they don't predict classes.
- Threshold tuner targets **precision@dissent ≥ 0.8** (not recall, not accuracy). Computed on the current batch (current `y_meta`), not the accumulated toxic buffer.
- Persistence guard: retrain only if disagreement on the `y_meta=1` slice exceeds `DISAGREEMENT_THRESHOLD = 0.2`. Buffer stores ONLY `y_meta=1` rows (named correctly: `toxic_samples_X`).
- Mode-vote tie-break: `np.bincount` (deterministic, dependency-free) → prefer `NO_TRADE_IDX` else smallest class index. `NO_TRADE_IDX = CLASSES.index("NO_TRADE")` — symbolic, not hardcoded.

**Integration map** (rails that already exist in Alpha):
- Training data → `chevelle_memory_labeler.trainable_only()` (firewall — non-negotiable; quarantined rows would poison the meta-target).
- Ground truth → `prediction_tracker.verified_24h.correct`.
- Calibration → `services/calibration_layer.apply()` (existing isotonic pipeline, append-only).
- Promotion ladder → `services/adversarial_promotion_gate` (existing `shadow → risk_only → veto → full`; 20 closed rows = first promotion threshold, same as Bull/Bear).
- Veto authority precedent → `fast_veto_layer.FAST_VETO_CAN_APPROVE = False` (hard-coded doctrine; new module mirrors it).
- Operator UI → new admin tab + reuse `calibration_kanban`.

**Validation plan** (synthetic-first, then organic):
- Class imbalance handling: challengers must dissent at the natural ~10% rate, not collapse to "never veto."
- Precision-vs-recall semantics: assert that precision is computed as `TP / (TP + FP)` on the current batch, NOT as `mean(dissent_preds == 1)` on the toxic-only buffer (which is recall in disguise).
- 24h lag contract: documented in trainer docstring; `verified_correct` must come from T-24h or earlier.
- Boolean dtype: `verified_correct.astype(bool)` before `~` (int arrays trigger bitwise NOT).
- Deterministic tie-breaks: `[LONG, LONG, SHORT, SHORT]` → `NO_TRADE`.
- **Proposer drift invariance** (load-bearing test): refit proposers on perturbed data; assert `((y_meta == 1) → (~verified_correct)).all()` and that the *gate signal* (`~verified_correct`) is bit-identical across proposer redraws. Do NOT assert `y_meta_v1 == y_meta_v2` directly — `y_meta` is allowed to vary with proposer skill; the *defining signal* is what must be stationary.
- Minimum sample size gate: don't train a challenger until the toxic buffer holds ≥ 100 verified positives. Below that, fall back to no veto (or existing Bull/Bear). Aligned with `adversarial_promotion_gate`'s existing 20-row first-promotion threshold but stricter for the challenger fit step.

**Estimated effort**: 3–5 days of careful wiring + 30–90 days organic accumulation before first shadow→risk_only promotion is statistically defensible. Front-load: 1 day reading existing prediction schema (LONG/SHORT/NO_TRADE actual storage shape) + feature assembly (currently scattered across `market_features`, regime fingerprint, macro, sentiment) before writing a line of new code.

**What WON'T work** (caught during the design arc):
- Replacing Bull/Bear with these challengers. Different ontologies — they coexist.
- Bypassing `trainable_only()`. Firewall is doctrinal.
- Granting direct veto authority on day one. Earned through the existing 4-phase ladder, not granted.
- Hardcoding `3` for `NO_TRADE`. Use `CLASSES.index(...)`.
- `scipy.stats.mode` for tie-breaks — its API changed in scipy ≥ 1.9 and returns a scalar with `keepdims=False`. Use `np.bincount`.
- Tuning thresholds on "accuracy" — 90% imbalanced meta-target makes a never-veto challenger 90% "accurate" and 0% useful. Tune on precision@dissent, period.
- Asserting `y_meta_v1 == y_meta_v2` after proposer drift. `y_meta` SHOULD vary with proposer skill (improving proposers → fewer toxic samples). The stationarity claim is about the *defining gate signal* (`~verified_correct`), not the meta-label set size.

**Why parked, not killed**:
- Framework loop is closed; arc was productive (5 iterations each removed a real bug, not just added sophistication).
- Honest sample-size constraint: at current verification cadence (~50–200 predictions/day, ~10% toxic rate), challenger fit needs months of organic accumulation. Building before then is premature optimization.
- The existing adversarial stack (Bull/Bear/Commander + Fast Veto + Adversarial Promotion Gate) is doing its job. This is a *second tier on top*, not a fix for a broken first tier.

**Trigger conditions for moving out of P3**:
- ADL organics window has completed (post-May 13, 2026).
- `predictions` collection has ≥ 1000 `verified_24h.correct == False` rows with `confidence > 0.7` (i.e., ≥ 1000 verified toxic positives).
- Operator green-lights with explicit "build the meta-classifier challenger layer."

**Reference docs**: design arc preserved in chat history (5 iterations between operator and agent, 2026-05-12). No code touched the repo during the design phase — intentional.

---

### 🟢 Approved 2026-02-08 — ready to schedule

- ~~**Tech debt: refactor `trading_bot_service.execute_trade()`.**~~
  ✅ DONE 2026-02-08. `execute_signal()` (the actual fill-path
  function) split into 5 cohesive helpers + slim orchestrator.
  113/113 trading-bot tests pass. See CHANGELOG entry (g).

- ~~**Tech debt: split `AppContent.jsx`.**~~
  ✅ DONE 2026-02-08. App.js 333 → 155 lines; new
  PreAuthRouter (~115 lines) + AuthenticatedShell (~145 lines).
  See CHANGELOG entry (g).

- ~~**Patent Watch admin dashboard (USPTO PatentsView API).**~~
  ✅ DONE 2026-02-08. Backend service + admin routes + UI panel
  + daily scheduler + 33 tests live. Activate by setting
  `USPTO_API_KEY` in `.env`. See CHANGELOG entry (f).

- ~~**Tier 1 Visual Polish.**~~ **DROPPED 2026-02-08 per user.**
  ("Don't worry about polishing anything. Just need this to
  function first then we can look at dressing it up.")
  Revisit only after function-first backlog clears.

---

### Existing P3 — strategic vision

Sourced from user-supplied PDFs (2026-04-20 drop):

### 🧭 RISEDUAL Navigator — *AI-native trading browser*
**Source:** `risedual_navigator_design.pdf`

A Netscape-Communicator-inspired desktop app that makes calibrated
signals a first-class browsing primitive.

- **Core metaphor:** *"A window onto calibrated trading signals."*
- Tabs → tickers. Bookmarks → watchlists. Persistent AI sidebar with
  tool-calling. Dedicated Signal + Pattern panel showing calibrated
  probabilities, regime, and detected patterns.
- Visible autonomy-tier guardrails (Alerts → Paper → Live).
- Cost transparency via a session ledger.
- **Architecture:** 3-tier — Presentation (UI) · Application (FastAPI) ·
  Core (SignalModel, RegimeModel, `patterns.py`, CalibrationGate).
- **Tech hint:** Tauri wrapper so we can reuse the React web app code.
- **Success metrics:**
  - LLM tool-call latency: **<400ms** cached signal fetch, **<1.5s**
    chat turn.
  - Milestones gated on calibration thresholds:
    - Tier 1: 100 labeled predictions
    - Tier 2: 500 labeled predictions
    - Tier 3 (live execution): 1000 labeled predictions

> **Dropped (2026-02-20):** "Adversitao Everywhere" cross-surface
> brand rollout (React Native mobile + Tauri Navigator desktop as
> a unified `@adversitao/ui` design system). No longer on the
> backlog at user request. RISEDUAL Navigator above stands on its
> own as a P3 desktop vision, independent of any mobile track.

---

## Backlog / parking lot

- API Usage Dashboard (tracks hit rate per external API)
- Caching decorator layer for Finnhub / OpenFIGI / QuiverQuant
- Cloudflare R2 / Backblaze B2 cold storage for options chains, tick
  data, news archives (decide post API-usage audit)
- Annual-billing toggle on Pricing section (proven +15-25% conversion
  uplift for SaaS)
