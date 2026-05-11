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

### 🧠 Meta-Classifier Challenger Layer (designed 2026-05-12, parked — v8 RISEDUAL-ready)

**Status**: design-complete (8-iteration arc with operator), implementation deferred. NOT to start until current ADL organics window completes AND operator green-lights.

**v8 (final, RISEDUAL-ready)**: all three correctness bugs and all three hygiene items from v7 closed. Status enum hardened (PENDING / RESOLVED / ERRORED / EXPIRED as module constants, fail-closed on unknown values). The code as written is the code to ship — no further design iterations expected before implementation.

**What it is**: a second-tier adversarial layer that complements the existing prompt-driven Bull/Bear/Commander cores. Where Bull/Bear opine on *direction* under prompt, the new challengers opine on `P(accepted decision was wrong | features)` as **calibrated binary meta-classifiers** trained against `verified_24h.correct` ground truth — and ONLY on rows where verification has actually settled.

**The nine doctrinal invariants** (load-bearing — every iteration that hit a wall hit one of these):
1. RESOLVED rows only train.
2. PENDING / ERRORED / EXPIRED rows train nothing.
3. `verified_correct` defines `y_meta`.
4. Proposer outputs are features only.
5. Invalid proposer outputs cannot pollute confidence.
6. Unknown verification states fail closed.
7. NO_TRADE wins unsafe / tied / empty cases.
8. HOLD cannot be promoted into trade.
9. Challenger / Council may reduce or block, never boost.

**Core design** (locked):
- 4 proposers + 3 challengers. Proposers multi-class (LONG/SHORT/HOLD/NO_TRADE/UNKNOWN); challengers binary meta (`was_wrong`: 0/1).
- All cores calibrated via `CalibratedClassifierCV(method="isotonic")` — symmetric calibration is non-negotiable.
- Challengers use `class_weight="balanced"` (HistGradientBoostingClassifier replaces GBC so the API works). Imbalance is the silent killer — though the verified-only reframe pushes meta-1 rate from ~10% up to ~30–50%, easing the knife-edge.

**Meta-target — STATIONARY + pending-safe + fail-closed (v8)**:
```python
# adversarial_meta_target.py
from __future__ import annotations
import numpy as np

PENDING = "PENDING"
RESOLVED = "RESOLVED"
ERRORED = "ERRORED"
EXPIRED = "EXPIRED"

VALID_STATUSES = {PENDING, RESOLVED, ERRORED, EXPIRED}
NON_TRAINABLE_STATUSES = {PENDING, ERRORED, EXPIRED}


def normalize_statuses(verification_status):
    status = np.asarray(verification_status).astype(str)
    status = np.char.upper(status)
    unknown = set(status.tolist()) - VALID_STATUSES
    if unknown:
        raise ValueError(f"Unknown verification_status values: {sorted(unknown)}")
    return status


def build_verified_veto_target(verified_correct, verification_status):
    """
    RISEDUAL-safe meta-target.

    Rules:
      * Only RESOLVED rows train.
      * PENDING / ERRORED / EXPIRED rows are excluded.
      * y_meta = 1 means the accepted decision was later proven wrong.
      * Proposer votes/confidence never define the label.

    Returns
    -------
    y_meta : np.ndarray of shape (n_resolved,)
    resolved_mask : np.ndarray of shape (n_total,), bool
        Caller MUST apply this mask to X and any aligned arrays
        before passing them to the challenger fit. The mask is a
        contract, not a hint.
    """
    verified = np.asarray(verified_correct, dtype=object)
    status = normalize_statuses(verification_status)

    if len(verified) != len(status):
        raise ValueError("verified_correct and verification_status must align")

    resolved_mask = (status == RESOLVED)
    resolved_verified = verified[resolved_mask]

    # Numpy arrays need elementwise comparison; `is None` does not broadcast.
    if np.any(resolved_verified == None):  # noqa: E711
        raise ValueError("RESOLVED rows cannot have verified_correct=None")

    resolved_verified = resolved_verified.astype(bool)
    y_meta = (~resolved_verified).astype(int)
    return y_meta, resolved_mask
```

**Proposer state → features (NOT label), shape-validated**:
```python
def build_challenger_features(base_X, majority_votes, avg_confs, disagreement_rate):
    base_X = np.asarray(base_X)
    majority_votes = np.asarray(majority_votes)
    avg_confs = np.asarray(avg_confs)
    disagreement_rate = np.asarray(disagreement_rate)

    n = base_X.shape[0]
    if majority_votes.shape[0] != n:
        raise ValueError("majority_votes length must match base_X rows")
    if avg_confs.shape[0] != n:
        raise ValueError("avg_confs length must match base_X rows")
    if disagreement_rate.shape[0] != n:
        raise ValueError("disagreement_rate length must match base_X rows")

    return np.column_stack([
        base_X, majority_votes, avg_confs, disagreement_rate,
    ])
```
The challenger sees proposer confidence and disagreement as *input columns*. It learns the boundary "where high-conf proposers tend to fail" from data, instead of being fed a hardcoded `TOXIC_CONFIDENCE_THRESHOLD = 0.7`. One fewer magic number in `config.py`.

**Hardened `majority_vote`**:
```python
# vote_utils.py
from __future__ import annotations
import numpy as np


def majority_vote(preds, confs, *, n_classes: int, no_trade_idx: int):
    if n_classes < 2:
        raise ValueError("n_classes must be >= 2")
    if not (0 <= no_trade_idx < n_classes):
        raise ValueError("no_trade_idx must be within class range")

    preds = np.asarray(preds, dtype=int)
    confs = np.asarray(confs, dtype=float)

    if preds.shape[0] != confs.shape[0]:
        raise ValueError("preds and confs must have the same length")

    if preds.size == 0:
        return no_trade_idx, 0.0

    valid = (preds >= 0) & (preds < n_classes) & np.isfinite(confs)
    preds = preds[valid]
    confs = confs[valid]

    if preds.size == 0:
        return no_trade_idx, 0.0

    counts = np.bincount(preds, minlength=n_classes)
    tied = np.flatnonzero(counts == counts.max())

    # Stricter v8 tie-break: ANY tie → NO_TRADE.
    vote = no_trade_idx if len(tied) > 1 else int(tied[0])
    avg_conf = float(np.mean(confs)) if confs.size else 0.0

    return vote, avg_conf
```
Filter applies to BOTH `preds` and `confs` (v7 bug fixed). NaN confidences and invalid class indices both die at the same `valid` mask. Empty input returns `no_trade_idx`. `n_classes >= 2` and `no_trade_idx` range checks fail loud at config-typo time.

**Stricter v8 tie-break**: ANY tie (two or more classes with the same top count) returns `no_trade_idx`, not smallest-index of the tied set. The earlier `no_trade_idx if no_trade_idx in tied else int(tied[0])` rule silently let `CLASSES[0]` win every 2v2 directional split — doctrinally that's a coin flip dressed up as a decision. Asymmetric cost (wrong action > wrong abstention) demands defaulting to safe abstention on EVERY ambiguous case.

**Downstream caveat**: any caller that currently branches on `-1` as a "no vote / missing data" sentinel would see `no_trade_idx` instead. Before adopting, `grep -rn "== -1\|!= -1" services/` for `majority_vote` consumers and reconcile.

- Veto = abstain (`NO_TRADE`). No alternative-class override — challengers are meta-classifiers, they don't predict classes.
- Threshold tuner targets **precision@dissent ≥ 0.8** (not recall, not accuracy). Computed as `TP / (TP + FP)` on the current resolved batch, NOT on the toxic-only accumulated buffer (which is recall in disguise).
- Persistence guard: retrain only if disagreement on the `y_meta=1` slice (of resolved rows) exceeds `DISAGREEMENT_THRESHOLD = 0.2`. Buffer stores ONLY resolved `y_meta=1` rows.
- Mode-vote tie-break: `np.bincount` (deterministic, dependency-free) → prefer `NO_TRADE_IDX` else smallest class index. `NO_TRADE_IDX = CLASSES.index("NO_TRADE")` — symbolic, not hardcoded.

**Integration map** (rails that already exist in Alpha):
- Training data → `chevelle_memory_labeler.trainable_only()` (firewall — non-negotiable; quarantined rows would poison the meta-target).
- Ground truth → `prediction_tracker.verified_24h.correct`. `verification_status` derived from `verified_24h` state (`None`→PENDING, populated→RESOLVED, plus ERRORED/EXPIRED for future use).
- Calibration → `services/calibration_layer.apply()` (existing isotonic pipeline, append-only).
- Promotion ladder → `services/adversarial_promotion_gate` (existing `shadow → risk_only → veto → full`).
- Veto authority precedent → `fast_veto_layer.FAST_VETO_CAN_APPROVE = False` (hard-coded doctrine; new module mirrors it).
- Operator UI → new admin tab + reuse `calibration_kanban`.

**Validation plan**:
- **Pending-row exclusion** (v7 load-bearing test):
  ```python
  def test_pending_rows_are_excluded_from_meta_training():
      verified_correct = np.array([True, False, False, True])
      verification_status = np.array(["RESOLVED", "PENDING", "RESOLVED", "PENDING"])
      y_meta, mask = build_verified_veto_target(verified_correct, verification_status)
      assert mask.tolist() == [True, False, True, False]
      assert y_meta.tolist() == [0, 1]
  ```
- **Proposer drift invariance** (v6 test): `y_meta` is bit-identical across proposer redraws because the function literally never sees proposer state.
- **Status enum hardening** (v8 tests):
  - Lowercase `"resolved"` is accepted (`np.char.upper` normalizes).
  - `"UNKNOWN_STATE"` raises `ValueError`.
  - `ERRORED` and `EXPIRED` rows are excluded from `resolved_mask`.
  - `RESOLVED` row with `verified_correct=None` raises `ValueError`.
- **Confs filter** (v8 bug-1 regression test):
  - `majority_vote([0, 1, -1, 99], [0.7, 0.8, 0.9, 0.6], n_classes=5, no_trade_idx=3)` → `avg_conf == mean([0.7, 0.8])`, not all four.
  - `majority_vote([0, 1], [0.7, np.nan], ...)` → `avg_conf == 0.7`, NaN dropped.
- **Shape validation** (v8 hygiene tests):
  - `majority_vote([0,1,2], [0.5,0.5], ...)` → `ValueError`.
  - `build_verified_veto_target` with mismatched lengths → `ValueError`.
  - `build_challenger_features` with any mismatched column → `ValueError`.
- **Config-typo guards**:
  - `majority_vote(..., n_classes=1, ...)` → `ValueError`.
  - `majority_vote(..., n_classes=5, no_trade_idx=99)` → `ValueError`.
- Deterministic tie-breaks: `[LONG, LONG, SHORT, SHORT]` → `NO_TRADE`. Empty preds → `NO_TRADE`.
- Minimum sample size gate: don't train a challenger until resolved set holds ≥ 100 rows with `verified_correct == False`. Below that, fall back to no veto (or existing Bull/Bear).

**Estimated effort**: 3–5 days of careful wiring + 30–90 days organic accumulation before first shadow→risk_only promotion is statistically defensible. Front-load: 1 day reading existing prediction schema + feature assembly (currently scattered across `market_features`, regime fingerprint, macro, sentiment) before writing a line of new code.

**What WON'T work** (caught during the design arc — preserved so a future agent doesn't re-discover them):
- Replacing Bull/Bear with these challengers. Different ontologies — they coexist.
- Bypassing `trainable_only()`. Firewall is doctrinal.
- Granting direct veto authority on day one. Earned through the existing 4-phase ladder, not granted.
- Hardcoding `3` for `NO_TRADE`. Use `CLASSES.index(...)`.
- `scipy.stats.mode` for tie-breaks — its API changed in scipy ≥ 1.9 and returns a scalar with `keepdims=False`. Use `np.bincount`.
- Tuning thresholds on "accuracy" — imbalanced meta-target makes a never-veto challenger ≥ 50% "accurate" and 0% useful. Tune on precision@dissent.
- Putting proposer state into the *label*. The label depends ONLY on `verified_correct` (gated by `verification_status == RESOLVED`). Proposer state goes in as features.
- `class_weight` on `GradientBoostingClassifier`. Not in its constructor. Use `HistGradientBoostingClassifier`, or pass `sample_weight` at fit time.
- Treating an empty `majority_vote` as `-1` if downstream callers branch on that sentinel. Audit consumers before switching to `no_trade_idx`.
- Training on pending rows under any default (treating them as 0, treating them as 1, dropping silently). Pending rows MUST be excluded by API contract.
- Silently treating unknown `verification_status` values as PENDING. v8 fails closed via `normalize_statuses`.
- Filtering `preds` by `valid` mask but leaving `confs` unfiltered. v7 bug. Both arrays die at the same mask.
- Mixing `None` into the bool cast. `dtype=object` upfront preserves `None`; explicit check raises before the cast.

**Why parked, not killed**:
- Framework loop is closed; arc was productive (8 iterations, each removed a real bug or sharpened a real invariant).
- Honest sample-size constraint: even at the looser verified-only definition, challenger fit needs hundreds of resolved positives before promotion is defensible.
- The existing adversarial stack (Bull/Bear/Commander + Fast Veto + Adversarial Promotion Gate) is doing its job. This is a *second tier on top*, not a fix for a broken first tier.

**Trigger conditions for moving out of P3**:
- ADL organics window has completed (post-May 13, 2026).
- `predictions` collection has ≥ 1000 rows with `verified_24h` resolved (status RESOLVED, either correct True or False).
- Operator green-lights with explicit "build the meta-classifier challenger layer."

**Reference docs**: 8-iteration design arc preserved in chat history (2026-05-12). No code touched the repo during the design phase — intentional. v8 is the version to ship.

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
