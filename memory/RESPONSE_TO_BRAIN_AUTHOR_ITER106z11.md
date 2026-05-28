# Response to Alpha sidecar author — iter-106z11 follow-up

**Topic**: Alpha is opinion-silent on the MC consensus channel.
**Status**: Diagnosis confirmed; verified contract below — copy-pasteable.

---

## The full MC brain-sidecar contract (production)

**Base URL:** `https://mission.risedual.ai`

Four endpoints make up the complete surface a brain sidecar talks to. The silence you're diagnosing lives on **#1**; the other three are listed here so future regressions on any of them are easy to spot.

### 1. Opinions — the channel Alpha is silent on

```
POST  https://mission.risedual.ai/api/ingest/opinion
Header:  X-Runtime-Token: <BRAIN>_INGEST_TOKEN
```

**Body schema** (Pydantic-validated server-side, rejects anything non-conforming with 400/422):

```python
{
    "runtime": "alpha",                          # literal: alpha|camaro|chevelle|redeye
    "topic": "symbol:AAPL",                      # format: "free" or "<kind>:<value>"
    "stance": "long",                            # one of: long, short, veto, endorse,
                                                 #         question, observation, agree,
                                                 #         disagree, refine, retract, hypothesis
    "confidence": 0.5,                           # 0.0..1.0
    "body": "Brief reasoning for the verdict",   # 1..MAX_BODY_CHARS (~2000)
    "evidence": {},                              # optional dict, JSON-serializable
    "in_reply_to": None,                         # optional opinion_id for threading
    "regime": "trend",                           # optional market regime tag
    "may_execute": False                         # MUST be False — schema-rejected otherwise
}
```

**Lands in collection:** `shared_brain_opinions`

**Critical schema rejects:**
- `may_execute` must be `false` — opinions never carry execution authority.
- `topic` must be `"free"` or `"<kind>:<value>"` (e.g. `"symbol:AAPL"`, `"regime:trend"`).
- `runtime` must be one of the 4 known brain names.
- `stance` must be in the allowed set.
- `body` cannot be empty.

**Stance mapping** (how directional verdicts map to the closed vocabulary):

| Stance | Maps to |
|---|---|
| `long` | BUY |
| `short` | SELL |
| `veto` | block |
| `endorse` | agree-with-prior |
| `observation` | neutral commentary / HOLD with reasoning |

### 2. Heartbeat — liveness

```
GET/POST  https://mission.risedual.ai/api/heartbeat-ping/{brain}?token=<BRAIN>_INGEST_TOKEN
```

Drives the `LIVE Xs` / `checkin_stale` flags on `mission.risedual.ai/admin/overview`.

### 3. Market-data key proxy — read data without broker keys

```
GET  https://mission.risedual.ai/api/admin/keys/market-data
Headers:  X-Brain-Id: <brain>
          X-Runtime-Token: <BRAIN>_INGEST_TOKEN
```

This is the endpoint that resolved iter-106z11's earlier confusion. Broker keys (`/keys/broker`) never leave MC — schema-pinned doctrine. Market-data keys come from this proxy.

### 4. Peer reads — brains can see each other's opinions

```
GET  https://mission.risedual.ai/api/runtime-discussion/opinions?caller=<brain>
GET  https://mission.risedual.ai/api/runtime-discussion/roles-manifest?caller=<brain>
Header:  X-Runtime-Token: <BRAIN>_INGEST_TOKEN
```

Used for `endorse` / `disagree` / `refine` stances that reference `in_reply_to` from another brain's prior opinion.

---

## Tokens

Per-brain ingest tokens, set in MC `.env` and the same value on the brain side:

```
ALPHA_INGEST_TOKEN
CAMARO_INGEST_TOKEN
CHEVELLE_INGEST_TOKEN
REDEYE_INGEST_TOKEN
```

---

## The fix for Alpha sidecar v1.6

The patch is **endpoint #1 only**. Camaro and Chevelle sidecars already call this; Alpha just isn't. Look at the helper in Camaro or Chevelle that constructs the `OpinionIn` body and POSTs with `X-Runtime-Token`. Mirror that pattern in Alpha alongside the existing sovereign-audit emit.

**No code change needed on MC.** The endpoint has been live since the early monorepo days; Alpha just isn't calling it.

The critical one is **#1** — that's the silence the MC watchdog now monitors.

---

## Two honest flags for the operator

1. **Same fix applies to RedEye and any other opinion-silent seat.** The Decisions Feed already shows the smoking gun for RedEye:

   ```
   GOVERNOR (redeye) silent for ≥ 30m — last seen: never
   ```

   Once Alpha lands, the patch is a literal copy/paste with `runtime="redeye"` for whoever owns the RedEye sidecar.

2. **This is production-side work.** Alpha's author is reading production state from `mission.risedual.ai`. The fix deploys into the Alpha sidecar pod (separate deployment from MC). None of this is MC code or trading-app code that can be shipped from those sides.

---

## Verification checklist post-deploy

After Alpha sidecar is patched and the pod redeployed:

1. `mission.risedual.ai/admin/intents` — Alpha's two seats (`EXECUTOR equity`, `STRATEGIST crypto`) flip from `opinion · never` to `opinion · Xs ago`.
2. Bottom counter `ALPHA: 0` starts climbing.
3. Fleet status `⚠ 2 opinion-silent` drops to `⚠ 1 opinion-silent` (RedEye still pending) then `✓ all signing` once RedEye is patched too.
4. Decisions Feed shows actual non-HOLD verdicts when STRATEGIST + EXECUTOR opinions agree.
5. `LIVE POSITIONS · OPEN` count starts increasing as the gate chain begins firing trades.
