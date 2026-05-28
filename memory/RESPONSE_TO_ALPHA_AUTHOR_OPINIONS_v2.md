# Response to Alpha sidecar author — Opinions v2

**Supersedes**: `RESPONSE_TO_BRAIN_AUTHOR_ITER106z11.md`
**Failure mode**: Alpha sidecar has posted **literally zero opinions** since deploy.
**Severity**: Hard block on the gate chain — Alpha holds two opinion-required seats.

---

## 1. Live evidence (production, `mission.risedual.ai`)

<operator: paste current values from /admin/intents before forwarding>

```
Current seat assignments for Alpha:
  - Crypto STRATEGIST   · opinion · never   ⚠
  - AUDITOR              · opinion · never   ⚠

shared_brain_opinions count where runtime == "alpha":
  0  (since pod boot at <ts>)

Fleet status header:
  ⚠ N opinion-silent  (Alpha contributes 2 of N)
```

Compare against peer brains posting correctly:

```
runtime=camaro  → 55 opinions over the same window
runtime=redeye  → posting per-intent
```

---

## 2. Why Alpha's silence specifically stalls the gate chain

Both of Alpha's current seats are **opinion-required** in the consensus chain:

- **Crypto STRATEGIST** — forms the directional reasoning for every crypto intent. With no `stance` posted, MC falls back to the deterministic doctrine sidecar which is conservative-by-design (HOLD on uncertainty).
- **AUDITOR** — issues the `endorse`/`disagree` co-signature on prior opinions. With no audit signal, the EXECUTOR gate has no second signature to seal the paradox record.

Net effect: every crypto intent goes to HOLD even when EXECUTOR + GOVERNOR pass. This is the silence the operator's MC watchdog now monitors (endpoint #1 below).

---

## 3. The verified MC contract

**Base URL**: `https://mission.risedual.ai`

### Endpoint #1 — Opinions (the one Alpha must wire)

```
POST  https://mission.risedual.ai/api/ingest/opinion
Header:  X-Runtime-Token: ${ALPHA_INGEST_TOKEN}
```

**Body schema** (Pydantic-validated, 400/422 on any mismatch):

```python
{
    "runtime": "alpha",                          # literal: alpha|camaro|chevelle|redeye
    "topic": "symbol:BTC-USD",                   # "free" or "<kind>:<value>"
    "stance": "long",                            # see role-specific vocabulary below
    "confidence": 0.65,                          # 0.0..1.0
    "body": "Brief reasoning for the verdict",   # 1..MAX_BODY_CHARS (~2000)
    "evidence": {},                              # optional dict
    "in_reply_to": None,                         # optional opinion_id for threading
    "regime": "trend",                           # optional market regime tag
    "may_execute": False                         # MUST be False — schema-rejected otherwise
}
```

**Lands in**: collection `shared_brain_opinions`.

### Endpoints #2–#4 (verify these work too)

```
GET/POST  /api/heartbeat-ping/alpha?token=${ALPHA_INGEST_TOKEN}
GET       /api/admin/keys/market-data    (X-Brain-Id: alpha + X-Runtime-Token)
GET       /api/runtime-discussion/opinions?caller=alpha
GET       /api/runtime-discussion/roles-manifest?caller=alpha
```

---

## 4. Role-specific stance vocabulary

Alpha occupies two seats with different semantic ranges. Use the right stance for the seat the opinion is from:

### Crypto STRATEGIST seat

| Stance | When to use |
|---|---|
| `long` | Directional BUY thesis |
| `short` | Directional SELL thesis |
| `observation` | HOLD with explicit reasoning |
| `hypothesis` | Tentative thesis pending more signal |

### AUDITOR seat (post in response to a STRATEGIST/EXECUTOR opinion via `in_reply_to`)

| Stance | When to use |
|---|---|
| `endorse` | The audit confirms the prior verdict |
| `disagree` | The audit contradicts the prior verdict |
| `refine` | The audit accepts but narrows scope (e.g. lower confidence) |
| `veto` | Hard block — must not fire |
| `retract` | Revoke a prior audit when fresh evidence arrives |

Critical pattern for the audit signal: the AUDITOR opinion **must** carry `in_reply_to=<the STRATEGIST or EXECUTOR opinion_id>` so the gate chain can pair them.

---

## 5. The `authority_call` mirror pattern

Alpha currently emits to the **sovereign-audit channel** correctly (`contribution` events) but does not mirror them to the opinions channel. The fix is to mirror every authority call into BOTH channels. Mirror pattern from Camaro:

```python
# Existing emit:
await mc_audit.post_contribution(
    runtime="alpha",
    seat=current_seat,
    verdict=authority_verdict,
    confidence=conf,
    symbol=symbol,
    ...
)

# Add this — mirrors the SAME authority call to the opinions channel:
await mc_opinions.post(
    "https://mission.risedual.ai/api/ingest/opinion",
    headers={"X-Runtime-Token": os.environ["ALPHA_INGEST_TOKEN"]},
    json={
        "runtime": "alpha",
        "topic": f"symbol:{symbol}",
        "stance": _stance_from_verdict(authority_verdict, current_seat),
        "confidence": conf,
        "body": reasoning_text,
        "in_reply_to": prior_opinion_id,   # only for AUDITOR seat
        "regime": current_regime,
        "may_execute": False,
    },
)
```

Camaro and RedEye sidecars already do this. Look at their post-loop helper and mirror its structure — do not invent new code.

---

## 6. Curl smoke test

Run this from any pod with `ALPHA_INGEST_TOKEN` set to verify the contract before deploying the patched sidecar:

```bash
curl -sS -X POST "https://mission.risedual.ai/api/ingest/opinion" \
  -H "X-Runtime-Token: $ALPHA_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "runtime": "alpha",
    "topic": "symbol:BTC-USD",
    "stance": "observation",
    "confidence": 0.5,
    "body": "smoke test — alpha sidecar opinion channel patch",
    "may_execute": false
  }'
```

Expected: `200` with an opinion document echoed back containing `opinion_id`.
Fail modes:
- `422` → schema mismatch (stance / topic / may_execute wrong)
- `401` → token wrong or missing
- `404` → URL typo (most common: `/api/opinions` instead of `/api/ingest/opinion`)

### Verification query

```bash
curl -sS "https://mission.risedual.ai/api/runtime-discussion/opinions?caller=alpha&limit=5" \
  -H "X-Runtime-Token: $ALPHA_INGEST_TOKEN" | jq '.[] | select(.runtime == "alpha")'
```

Should return the smoke-test opinion at the top of the list.

---

## 7. How to know it's fixed (operator dashboard signal)

After patched Alpha pod redeploys:

1. **`mission.risedual.ai/admin/intents`** — Alpha's two seats flip from `opinion · never` to `opinion · Xs ago`.
2. **Fleet status header** — `⚠ N opinion-silent` decrements by 2.
3. **Bottom counter** — `ALPHA: 0` starts climbing.
4. **Decisions Feed** — non-HOLD crypto verdicts begin appearing when Crypto STRATEGIST + EXECUTOR opinions agree.
5. **Audit pairing** — AUDITOR opinions begin showing as `in_reply_to=<...>` next to prior STRATEGIST/EXECUTOR ones.
6. **`LIVE POSITIONS · OPEN`** — count starts increasing as the gate chain begins firing.

---

## 8. Honest flags for the operator

1. **Same pattern, same fix** applies to **Chevelle** (separate doc: `RESPONSE_TO_CHEVELLE_AUTHOR_OPINIONS.md`). Chevelle has a different failure mode (`1 post then stopped` vs Alpha's `zero ever`) but the patch is the same `authority_call` mirror.
2. **This is production-side work.** All four endpoints are on `mission.risedual.ai`. The fix deploys into the Alpha sidecar pod (separate from MC). None of this is MC code or trading-app code.
3. **Token rotation**: `ALPHA_INGEST_TOKEN` is symmetric between MC `.env` and Alpha pod `.env`. If you ever rotate it, both sides must be updated and Alpha must be redeployed.
