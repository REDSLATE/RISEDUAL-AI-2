# RISEDUAL Honesty Patch — Brain Operator Handoff Bundle

**Target audience:** Camaro, Chevelle, REDEYE engine teams
**Status:** Wire format implemented + tested on Alpha (2026-05-15)
**Backwards compat:** Full. All honesty fields are optional in MC's
`IntentIn` schema; brains that don't ship them keep working at the
cost of their audit trail.

---

## What you're shipping

When your brain POSTs an intent to MC's `/api/intents`, attach the
honesty receipt as additional fields. MC stores them in
`shared_intents` and surfaces them at
`/api/admin/intents/honesty?stack=<brain>&hours=24`.

The fields are doctrinally required (silent state = neutral drift),
but technically optional during rollout.

---

## Files in this bundle

```
sovereign/
  ├── intent_receipt.py          # ← doctrine receipt → MC wire-format bridge
  ├── intent_bridge.py           # ← 5-line emission helper for brain runtimes
  └── mc_client.py               # ← contains build_intent_body + post_intent

tests/
  └── test_intent_receipt_bridge.py   # ← 28 tests covering the round-trip
```

Drop them into your `sovereign/` package (or equivalent), rename
imports if needed, run the tests.

---

## Minimum integration (5 lines at the call site)

Wherever your brain currently computes a directional verdict:

```python
from sovereign.intent_bridge import emit_intent_from_consensus
from sovereign.mc_client import MCClient

mc = MCClient(base_url="https://mission.risedual.ai",
              brain="camaro", runtime_token=os.environ["CAMARO_INGEST_TOKEN"])
receipt = your_consensus_function(...)   # must include doctrine receipt fields
await emit_intent_from_consensus(mc, receipt, qty=desired_size)
```

The bridge:
* Skips emission silently when `raw_action ∉ {BUY, SELL, SHORT, COVER}`.
  HOLD is not an intent — that's the failure mode we're avoiding.
* Stamps `execution_decision="OBSERVE_ONLY"` automatically. RISEDUAL
  is doctrinally headless (V3). If MC's executor seat fires the
  order, that's MC's call. The brain only reports.
* Fire-and-forget — MC failures log at WARNING and don't block the
  next tick.

---

## Honesty receipt fields (MC accepts all)

Action-domain (all optional, validated against
`{BUY, SELL, HOLD, SHORT, COVER}`):

| Field | Meaning |
|---|---|
| `raw_action` | What the brain JUDGED before gates |
| `market_decision` | Same, but the doctrine name for it |
| `display_action` | What MC should display (after gates) |
| `execution_decision` | `ALLOW` / `BLOCK` / `SIZE_DOWN` / `OBSERVE_ONLY` |

Bounded confidences (all optional, validated `[0, 1]`):

| Field | Scale |
|---|---|
| `raw_confidence` | unit |
| `pre_weight_confidence` | unit |
| `post_weight_confidence` | unit |
| `council_penalty` | signed unit delta `[-1, 1]` |

Per-source weights (all optional, validated `[0, 3]`):
`strategist_weight`, `auditor_weight`, `commander_weight`,
`regime_weight`, `memory_weight`.

Free-form audit:
* `hold_reason` (string) — why the display action was HOLD
* `blocked_by` (list of strings) — which gates fired
* `would_have_traded_without_gates` (bool)

---

## Scale-conversion gotchas (the bridge handles these for you)

If your local doctrine receipt uses 0-100 percent (RISEDUAL's does):
* `raw_confidence: 73` (percent) → `raw_confidence: 0.73` (unit) on wire
* `council_penalty: -8.0` (percentage-point) → `-0.08` (unit) on wire

If you skip the bridge and call `build_intent_body` directly, you
must pass unit-scale already. The validator rejects values outside
`[0, 1]` with `MCContractError`.

---

## Brain → role-slot mapping (4-brain council → 5-slot MC schema)

The bridge maps positionally:

| Brain | MC role |
|---|---|
| alpha | strategist_weight |
| camaro | auditor_weight |
| chevelle | commander_weight |
| redeye | regime_weight |
| (none) | memory_weight |

`memory_weight` defaults to `1.0` only if at least one of the four
brain weights was supplied — we never fake balance.

---

## What MC sees after rollout

Before the patch (legacy payload):
```
GET /api/admin/intents/honesty?stack=camaro&hours=24
{
  "total_intents": 1552,
  "blocked_directional": 0,
  "by_reason": {}
}
```

After the patch (typical first day):
```
GET /api/admin/intents/honesty?stack=camaro&hours=24
{
  "total_intents": 234,
  "blocked_directional": 47,
  "by_reason": {
    "MIN_CONFIDENCE_TO_TRADE": 22,
    "FEATURE_HEALTH_CLAMP": 14,
    "PDT_GATE": 6,
    "EXPOSURE_CAP": 5
  },
  "penalty_distribution": [...]
}
```

The "blocked_directional > 0" line is the success signal — it means
the brain is no longer pretending HOLD when it actually wanted to
trade.

---

## Bounded penalty (the other half of the doctrine)

Inside your consensus/scoring layer, replace any:

```python
if council_disagrees:
    confidence = 0.50   # ← the silent-state bug
```

with:

```python
if council_disagrees:
    confidence *= 0.82   # bounded penalty, NEVER flatten
# or:
    confidence -= 0.12   # additive variant, equivalent intent
```

RISEDUAL's full reference is in `services/confidence_weighting.py`
(`apply_disagreement_penalty`). It distinguishes:

* `UNANIMOUS` → ×1.00
* `HOLD_DISSENT` → ×0.90 (softer, one brain says hold)
* `HARD_CONFLICT` → ×0.70 (both BUY and SELL present)
* `ALL_HOLD` → ×1.00 (no signal to penalize)

Steal the module if you want; it has no MC-side dependencies.

---

## Tests you should pass

Drop `test_intent_receipt_bridge.py` next to your other sidecar tests
and run pytest. The critical asserts:

1. `build_intent_body` rejects out-of-range confidence / weights
2. The bridge converts percent → unit on confidence + council_penalty
3. The bridge maps brain → role slots positionally
4. A bad value in one weight doesn't poison the rest of the payload
5. Receipt → bridge → body round-trips without validation errors

All 28 tests pass on Alpha right now.

---

## Verify after deploy

```bash
# Should jump from 0 → N within the first tick
curl https://mission.risedual.ai/api/admin/intents/honesty?stack=<brain>&hours=1 \
  | jq '.blocked_directional, .by_reason'
```

If `blocked_directional` stays at 0 with `total_intents` growing,
the brain is still on the legacy payload shape — verify the bridge
import path and check `build_intent_body` isn't being called with
the core 4 fields only.

If MC returns `422 Unprocessable`, you have a scale bug somewhere —
percent in a `[0, 1]` field is the most common cause.

---

## Operator UI

RISEDUAL ships a local mirror of MC's honesty endpoint at
`Admin → Honesty` (owner-only). It surfaces the same JSON in the
brain's own dashboard so operators don't have to leave to audit
their own brain. Status badge clearly distinguishes "MC live" vs
"MC unreachable" vs "MC unconfigured" so an empty table is never
ambiguous.

Same pattern is welcome on Camaro / Chevelle / REDEYE if your UI
stack has the bandwidth — `routes/sovereign_honesty.py` is a
30-line owner-only proxy.

— Alpha team
