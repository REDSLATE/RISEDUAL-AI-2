# Response to Chevelle sidecar author — Opinions

**Failure mode**: Chevelle posted **1 opinion** at sidecar boot, then stopped. The HTTP path works; the per-intent loop isn't wired.
**Severity**: Soft block on the gate chain — Chevelle holds the GOVERNOR seat on both lanes, and every intent now misses the governor verdict.

---

## 1. Live evidence (production, `mission.risedual.ai`)

<operator: paste current values from /admin/intents before forwarding>

```
Current seat assignments for Chevelle:
  - Equity GOVERNOR  · opinion · <Xm ago>  ⚠ (only 1 ever)
  - Crypto GOVERNOR  · opinion · <Xm ago>  ⚠ (only 1 ever)

shared_brain_opinions count where runtime == "chevelle":
  1  (posted at <ts of pod boot>, none since)

Comparison — peers posting per-intent:
  runtime=camaro  → 55 opinions over the same window
  runtime=redeye  → posting per-intent
```

The shape of the failure — exactly one post then permanent silence — is the **classic startup-only-emit** pattern: the sidecar posts once during pod bootstrap (likely a "hello, I'm alive" emit) and then never re-enters the opinion-post path inside the per-intent loop.

---

## 2. Why Chevelle's silence specifically stalls the gate chain

GOVERNOR is the seat that issues the **veto / reduce / observation** verdict on each intent — it's the last opinion the gate chain needs to seal a paradox record before EXECUTOR can fire.

When GOVERNOR is silent:
- The Decisions Feed shows `gate_pass` on every per-gate check (broker present, spread ok, operator armed) but the verdict stays `HOLD`.
- The paradox record can't be co-signed, so even a unanimous STRATEGIST + EXECUTOR consensus stays in the queue.

You can confirm this in the Decisions Feed right now — look for rows like:

```
gate_pass · <symbol> · HOLD · pass · GOVERNOR (chevelle) silent for ≥ Xm
```

That message is the smoking gun.

---

## 3. The verified MC contract

**Base URL**: `https://mission.risedual.ai`

```
POST  https://mission.risedual.ai/api/ingest/opinion
Header:  X-Runtime-Token: ${CHEVELLE_INGEST_TOKEN}
```

**Body schema** (Pydantic-validated, 400/422 on any mismatch):

```python
{
    "runtime": "chevelle",                       # literal: alpha|camaro|chevelle|redeye
    "topic": "symbol:AAPL",                      # "free" or "<kind>:<value>"
    "stance": "veto",                            # see governor vocabulary below
    "confidence": 0.7,                           # 0.0..1.0
    "body": "Brief reasoning for the verdict",   # 1..MAX_BODY_CHARS (~2000)
    "evidence": {},                              # optional dict
    "in_reply_to": None,                         # optional opinion_id for threading
    "regime": "trend",                           # optional market regime tag
    "may_execute": False                         # MUST be False — schema-rejected otherwise
}
```

**Lands in**: collection `shared_brain_opinions`.

---

## 4. Governor-specific stance vocabulary

The GOVERNOR seat's job is to **assess** the prior STRATEGIST/EXECUTOR verdict and either rubber-stamp, reduce, or block. The right stances for that role:

| Stance | When to use |
|---|---|
| `endorse` | GOVERNOR agrees with the prior verdict — gate chain seals at the prior confidence |
| `refine` | GOVERNOR accepts the verdict but narrows scope (e.g. reduce position size) — gate chain seals at a lower confidence |
| `disagree` | GOVERNOR disagrees but does not block — paradox record records the dissent |
| `veto` | Hard block — must not fire under any circumstance |
| `observation` | Neutral commentary, no directional verdict — gate chain stalls, awaiting next opinion |

Critical pattern: GOVERNOR opinions **should** carry `in_reply_to=<the STRATEGIST or EXECUTOR opinion_id>` so the gate chain can pair the governor verdict to the right intent. Without `in_reply_to`, the opinion lands but doesn't bind to a paradox record.

---

## 5. The `authority_call` mirror pattern (the fix)

Chevelle's code path *works* — it posted once at boot, which proves the HTTP layer, schema, and token are correct. The failure is that the per-intent loop doesn't re-emit. Mirror pattern:

```python
# Inside the per-intent loop (NOT just at boot):
async def on_intent_received(intent):
    # ── Existing governance check (already working) ─────────
    verdict, conf, reasoning = await chevelle_brain.govern(intent)

    # ── ADD: mirror the governance call to the opinions channel
    await mc_opinions.post(
        "https://mission.risedual.ai/api/ingest/opinion",
        headers={
            "X-Runtime-Token": os.environ["CHEVELLE_INGEST_TOKEN"],
        },
        json={
            "runtime": "chevelle",
            "topic": f"symbol:{intent.symbol}",
            "stance": _stance_from_governor_verdict(verdict),
            "confidence": conf,
            "body": reasoning,
            "in_reply_to": intent.strategist_opinion_id,  # IMPORTANT
            "regime": intent.regime,
            "may_execute": False,
        },
    )
```

Where `_stance_from_governor_verdict` maps Chevelle's internal verdict tokens onto the 5-stance governor vocabulary above.

The fact that the boot-time post succeeded means the HTTP client, token, and schema-conformant body construction all exist somewhere in the codebase. The patch is moving that exact code path into the per-intent loop — not rewriting it from scratch.

---

## 6. Curl smoke test

Confirm Chevelle's token + endpoint still work (they did at boot — sanity check):

```bash
curl -sS -X POST "https://mission.risedual.ai/api/ingest/opinion" \
  -H "X-Runtime-Token: $CHEVELLE_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "runtime": "chevelle",
    "topic": "symbol:AAPL",
    "stance": "observation",
    "confidence": 0.5,
    "body": "smoke test — chevelle per-intent loop patch verification",
    "may_execute": false
  }'
```

Expected: `200` with `opinion_id` echoed.
If this works (which it should, since boot worked), the contract is fine — the fix is purely the per-intent wiring.

### Verification query

```bash
curl -sS "https://mission.risedual.ai/api/runtime-discussion/opinions?caller=chevelle&limit=10" \
  -H "X-Runtime-Token: $CHEVELLE_INGEST_TOKEN" | jq '.[] | select(.runtime == "chevelle") | {opinion_id, stance, topic, created_at, in_reply_to}'
```

After the fix, you should see one opinion **per recent intent**, each with a populated `in_reply_to` pointing at a STRATEGIST/EXECUTOR opinion.

---

## 7. How to know it's fixed (operator dashboard signal)

After patched Chevelle pod redeploys:

1. **`mission.risedual.ai/admin/intents`** — Chevelle's `opinion · Xm ago` rolls forward in real-time alongside each intent.
2. **Decisions Feed** — `GOVERNOR (chevelle) silent for ≥ Xm` messages disappear.
3. **Per-intent count** — `shared_brain_opinions` count where `runtime=chevelle` should grow by ~1 per intent.
4. **Bottom counter** — `CHEVELLE: <N>` climbs in step with Camaro / RedEye.
5. **Paradox records** — start sealing with GOVERNOR signatures attached; `LIVE POSITIONS · OPEN` count rises as the gate chain begins firing intents that have been queued.

---

## 8. Honest flags for the operator

1. **Same root cause, same surface as Alpha** — both fail to mirror authority calls into the opinions channel. Alpha never posts; Chevelle posted once. Once Chevelle's per-intent wiring lands, the same review applies to Alpha (separate doc: `RESPONSE_TO_ALPHA_AUTHOR_OPINIONS_v2.md`).
2. **This is production-side work.** Patch deploys into the Chevelle sidecar pod (separate from MC). None of this is MC code or trading-app code.
3. **Smoke test before redeploy** — since Chevelle's boot-time emit proves the contract works, the smoke-test curl above should succeed today against current prod. If it doesn't, the token may have rotated.
