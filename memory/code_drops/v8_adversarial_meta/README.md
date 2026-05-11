# v8 Adversarial Meta-Target Primitives

Portable, dependency-minimal building blocks for training binary
meta-classifier "challengers" alongside a multi-class "proposer"
ensemble. Output of an 8-iteration design arc on RISEDUAL; written so
it drops cleanly into any decision-system stack (trading, content
moderation, fraud detection, medical triage, ...).

**Dependencies**: `numpy` only. No sklearn. No pandas. No Mongo.

**License / status**: internal — not for redistribution.

---

## The nine doctrinal invariants

1. **RESOLVED rows only train.**
2. **PENDING / ERRORED / EXPIRED rows train nothing.**
3. **`verified_correct` defines `y_meta`.**
4. **Proposer outputs are features only.**
5. **Invalid proposer outputs cannot pollute confidence.**
6. **Unknown verification states fail closed.**
7. **NO_TRADE wins unsafe / tied / empty cases.**
8. **HOLD cannot be promoted into trade.** *(caller-level — enforced at the veto layer, not in these primitives)*
9. **Challenger / Council may reduce or block, never boost.** *(caller-level — enforced at the authority gate)*

Invariants 1–7 are enforced by the code in this folder. Invariants
8–9 are framework-level rules your veto layer / decision policy must
uphold; they're listed here so they aren't forgotten.

---

## What's in the box

| File | Purpose |
|---|---|
| `adversarial_meta_target.py` | `build_verified_veto_target()` — turns `(verified_correct, verification_status)` into a stationary binary label `y_meta` plus the `resolved_mask` the caller MUST apply. Also exposes `normalize_statuses()` and the status enum constants. |
| `vote_utils.py` | `majority_vote()` — deterministic, fail-closed, tie-broken majority vote. NO_TRADE wins ties / empty / all-invalid cases. Filters NaN confidences and invalid class indices at the boundary. |
| `challenger_features.py` | `build_challenger_features()` — concatenates base features with proposer-state columns (votes, avg_confs, disagreement_rate). Shape-validates every column. |
| `tests/test_v8_doctrine.py` | 30+ pinned invariants. If any of these fail after a refactor, the framework has regressed. |

---

## Integration protocol

```python
from v8_adversarial_meta import (
    build_verified_veto_target,
    build_challenger_features,
    majority_vote,
    RESOLVED,
)

# 1. Build label from verified outcomes only.
y_meta, resolved_mask = build_verified_veto_target(
    verified_correct=df["verified_correct"].values,
    verification_status=df["verification_status"].values,
)

# 2. Build challenger features for ALL rows.
X_full = build_challenger_features(
    base_X=df[base_feature_cols].values,
    majority_votes=df["majority_vote"].values,
    avg_confs=df["avg_conf"].values,
    disagreement_rate=df["disagreement_rate"].values,
)

# 3. Apply mask. CRITICAL — y_meta length is shorter than X_full
#    by design, so skipping this step raises a sklearn shape error
#    rather than silently misaligning rows.
X_train = X_full[resolved_mask]

# 4. Fit your binary challenger.
challenger.fit(X_train, y_meta)   # use class_weight="balanced"

# 5. Compute majority vote at decision time.
vote, conf = majority_vote(
    preds=[p1, p2, p3, p4],
    confs=[c1, c2, c3, c4],
    n_classes=N_CLASSES,
    no_trade_idx=NO_TRADE_IDX,
)
```

---

## Why these primitives exist as a separate package

Earlier iterations of this design (v1–v7) accumulated subtle bugs
that only surface at integration time:

- **v1–v2**: static "flip the labels" challengers — performative, not
  structural adversaries.
- **v3**: label depended on live majority vote — non-stationary; the
  meta-target moved when proposers retrained.
- **v4**: precision-vs-accuracy threshold tuning collapsed to "never
  veto" under class imbalance.
- **v5**: `class_weight="balanced"` doesn't exist on
  `GradientBoostingClassifier`; `scipy.stats.mode` API changed in 1.9
  and broke tie-breaks; "precision@dissent" code actually computed
  recall.
- **v6**: label reframed to `~verified_correct` — stationary, but the
  pending-row exclusion was a docstring promise rather than an API
  contract.
- **v7**: pending exclusion baked into the API via `(y_meta,
  resolved_mask)` return tuple. But `majority_vote` filtered `preds`
  without filtering `confs`, so dropped-prediction confidences still
  polluted `avg_conf`. Unknown status values silently routed to
  PENDING. Length mismatches produced confusing index errors.
- **v8 (this)**: every above failure mode has a regression test.
  Status enum is fail-closed. Confs filter is paired with preds
  filter. `dtype=object` preserves `None` so the explicit None-check
  fires before the bool cast. All shape / range / dtype mistakes
  raise `ValueError` at the boundary.

The arc is documented because future maintainers WILL want to
"simplify" one of the asserts and bring back a v5-era bug. The tests
exist so they can't.

---

## Tuning notes

These primitives are deliberately silent about hyperparameters. The
framework around them needs:

- **Class balancing**: challengers should fit with
  `class_weight="balanced"` (RandomForest, SVC, HistGradientBoosting
  all support it; vanilla GradientBoosting does NOT — use sample
  weights at fit time).
- **Calibration**: wrap every challenger in
  `CalibratedClassifierCV(method="isotonic")`. Proposer calibration
  must match.
- **Threshold tuning**: target **precision@dissent ≥ 0.8**, NOT
  accuracy, NOT recall. Compute precision as `TP / (TP + FP)` on the
  current resolved batch.
- **Persistence**: only retrain if disagreement on the `y_meta=1`
  slice exceeds ~20%. Don't churn the model on noise.
- **Minimum sample size**: don't run challenger fit until the
  resolved set holds ≥ 100 rows with `verified_correct == False`.
  Below that, fall back to a more conservative authority layer.
- **Promotion ladder**: never grant veto authority on day one. Use a
  shadow → risk_only → veto → full progression, gated by lifetime
  precision metrics, not 24h windows.

---

## Running the tests

```bash
# From the parent directory of v8_adversarial_meta/
python -m pytest v8_adversarial_meta/tests -v
```

Expected: 30+ tests pass. If any fail after you've copied the folder
into your stack, you have an import-path issue (check that the test
file's `from v8_adversarial_meta import ...` resolves correctly).

---

## Version

`v8.0.0` — designed 2026-05-12. No expected design changes before
implementation. If a v9 happens, it should preserve the nine
invariants verbatim and only narrow the type contracts (e.g. add
type hints for non-numpy-array inputs).
