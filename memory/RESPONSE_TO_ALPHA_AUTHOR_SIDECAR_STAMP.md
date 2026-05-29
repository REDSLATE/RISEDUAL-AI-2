# Response to Alpha sidecar author — Sidecar Environment Stamp

**Supersedes for active follow-up**: `RESPONSE_TO_ALPHA_AUTHOR_OPINIONS_v2.md`
**Status**: Patch shipped, code path verified, single env-config blocker remains.
**Severity**: 1-line env var away from green. Not a code problem.

---

## 1. Live evidence (production, `mission.risedual.ai`)

<operator: paste current values from /admin/intents and /admin/diagnostics before forwarding>

```
Alpha sidecar → MC roundtrip status:
  - heartbeat:     fresh (last seen <Xs> ago)
  - policy_hash:   matches MC canonical
  - opinion POST:  reaching MC endpoint
  - env stamp:     INVALID — BAD_OR_UNKNOWN_DB_NAME

shared_brain_opinions count where runtime == "alpha":
  0  (MC stamp gate is rejecting every POST at the door)

MC rejection log (representative):
  reject reason: BAD_OR_UNKNOWN_DB_NAME
  received: <empty | "preview" | "test" | "unknown">
  expected: one of MC's known prod database identifiers
```

**This is materially different from the iter-106z11 / v2 diagnosis.** Alpha's author DID ship the patch — the opinions code path is live, the HTTP layer works, the token is correct. What's blocking is a single env-stamp validation downstream of the POST. MC is doing exactly what it's designed to do: refuse to accept opinions from a sidecar that can't prove it's running in a sanctioned prod environment.

---

## 2. The specific failure

```
MC verdict: BAD_OR_UNKNOWN_DB_NAME
```

MC reads `RISEDUAL_DB_NAME` (and adjacent stamp vars) on every inbound opinion POST. If the value is missing, empty, or one of the explicitly-blacklisted strings (`preview`, `test`, `unknown`, `dev`, etc.), the POST is rejected at the validation layer *before* the opinion lands in `shared_brain_opinions`. This protects prod broker keys from preview/test sidecars triggering real orders.

On Alpha's prod pod, `RISEDUAL_DB_NAME` is currently one of those forbidden values (or unset).

---

## 3. Exact env vars to set on Alpha's prod pod

```bash
# Required — must exactly match MC's canonical prod DB identifier
RISEDUAL_DB_NAME=<the verified prod db_name — confirm with operator>

# Required — symmetric with MC .env, already set, included here for completeness
ALPHA_INGEST_TOKEN=<unchanged>

# Adjacent stamp vars MC may also validate
RISEDUAL_ENV=prod
RISEDUAL_RUNTIME_NAME=alpha
```

**Critical**: the `RISEDUAL_DB_NAME` value is **not** something Alpha's author should guess. The operator will confirm the exact string from MC's `.env`. Common mistake to avoid: leaving the value as `preview` because that's what the dev pod uses — prod pod must use the prod DB name.

After setting the vars, restart / redeploy the Alpha sidecar pod so the new environment takes effect.

---

## 4. Verification curl

Run this from any pod with `ALPHA_INGEST_TOKEN` set, **after** Alpha's prod pod redeploys with the corrected env:

```bash
curl -sS -X POST "https://mission.risedual.ai/api/ingest/opinion" \
  -H "X-Runtime-Token: $ALPHA_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "runtime": "alpha",
    "topic": "symbol:BTC-USD",
    "stance": "observation",
    "confidence": 0.5,
    "body": "post-stamp-fix smoke test",
    "may_execute": false
  }'
```

**Expected response shape**:

```json
{
  "ok": true,
  "verdict": "prod",
  "opinion_id": "<uuid>",
  "runtime": "alpha",
  "received_at": "<iso8601>"
}
```

The `verdict: "prod"` field is the green-light signal. If it returns:
- `verdict: "BAD_OR_UNKNOWN_DB_NAME"` → the env var didn't propagate to the running process (check pod restart, deployment manifest layer, secret scope).
- `401` → token wrong or missing.
- `404` → URL typo.
- `422` → schema regression (shouldn't happen; the body above is verified).

---

## 5. Secondary blocker — operator-side, NOT a brain-team item

Even after Alpha's stamp goes green and opinions start landing, **Alpha does not currently hold an executor seat** on the Mission Control Seat Roster. That's an operator dashboard change (2-click reseat), not a code change.

**Important: Alpha's author should NOT try to fix this from their side.** Seat assignments are operator-controlled in MC; there's no env var or sidecar-side knob for them. Trying to push a "fix" for the seat from the brain pod will either no-op or fail validation.

This blocker is recorded here so Alpha's author knows:
- Their stamp going green is the end of their deliverable.
- If trades still don't fire after that, the next step is on the operator (seat assignment), not on them.
- The status sequence after redeploy will be: stamp valid → opinions landing → seat reassignment → trades flowing.

---

## 6. Doctrine pin: why MC enforces the stamp

MC enforces `RISEDUAL_DB_NAME` validation on every opinion POST because **opinions ultimately feed the gate chain that authorizes real orders against real broker keys**. A misconfigured sidecar running in preview, test, or dev — but pointed at the prod MC by token — would otherwise leak preview-quality opinions into the prod consensus, and those opinions could co-sign paradox records that fire prod orders.

The stamp gate is the boundary that says: "I will not accept opinions from a sidecar that cannot prove it's running against the canonical prod database identifier MC expects." It's the same doctrinal pattern as:
- Broker keys never leaving MC (iter-106z11)
- `may_execute=False` schema-pinned on every opinion (current contract)
- Owner-only auth on every admin write surface

Three nested boundaries, all enforcing the same invariant: **execution authority requires verified provenance at every hop**. The stamp gate is one hop in that chain; it's catching Alpha now because Alpha's prod pod env is the missing piece, not because anything else is wrong.

---

## Definition of done

Alpha sidecar author's work is complete when, after the env var fix and pod redeploy:

1. The verification curl in §4 returns `verdict: "prod"` with a valid `opinion_id`.
2. `shared_brain_opinions` count where `runtime == "alpha"` begins incrementing as the per-intent loop fires.
3. `mission.risedual.ai/admin/intents` shows Alpha's seat strip flipping from "stamp invalid" (or equivalent) to a live `opinion · Xs ago` indicator.

The seat reassignment in §5 is the operator's next step, not Alpha's.

---

## Honest flags for the operator

1. **Pattern continuity with iter-106z11 / opinions v2**: each successive Alpha thread has narrowed the gap by one structural layer.
   - iter-106z11 → wrong endpoint, wrong header, wrong schema (contract)
   - opinions v2 → contract correct, but no per-intent emit (code)
   - sidecar stamp → code correct, but env stamp invalid (config)

   Each step the failure surface has gotten smaller and more specific. This last one is genuinely one env var.

2. **Reference bundle from Chevelle (post-iter-106z12) is still on the table** as a known-working snapshot — `services/risedual_monorepo_client.py` + `mc_key_proxy.py`. If Alpha's author wants to audit their stamp-handling against a working brain's implementation before the env fix lands, that's a good cross-check. Frame as reference, not dependency.

3. **No changes on the trading-app side.** This is entirely a brain-pod env configuration. The trading-app Shelly Federation does not interact with the MC opinion channel, so nothing about this thread blocks or accelerates a `risedual.ai` redeploy.
