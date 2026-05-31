# Mission Control — Brain API Quickstart (v1)

> Authoritative source: doc shared by MC owner on 2026-05-31.
> Wire contract for Alpha (and any other brain) talking to MC.
> Tripwire tests for this doc live in
> `/app/backend/tests/test_mc_brain_api_quickstart_v1.py`.

Brain-agnostic. The same endpoints, headers, and contracts work for
**Alpha, Camaro, Chevelle, and RedEye**. Substitute your brain name
wherever you see `<brain>` (lowercase: `alpha` | `camaro` | `chevelle`
| `redeye`).

For the identity / health surface, see `BRAIN_IDENTITY_HANDOFF.md`
and `mc_identity_v1.py`. This doc covers the runtime/operational
endpoints brains use to participate in MC.

---

## 0. Base URL and auth

| Environment | Base URL                              |
| ----------- | ------------------------------------- |
| Production  | `https://mission.risedual.ai`         |
| Preview     | `https://<preview-host>` (operator-provided) |

**Two auth schemes — never both:**

- **Runtime-token (brain path)**:
  ```
  X-Brain-Id:       <brain>
  X-Runtime-Token:  <token MC accepts for THIS brain>
  ```
- **JWT (operator path, brains don't use)**:
  ```
  Authorization: Bearer <jwt>
  ```

If you send both, MC honors the JWT path. Mismatched `X-Brain-Id` and
`X-Runtime-Token` → HTTP 401.

---

## 1. Market data (READ)

`GET /api/admin/market-data/snapshot/{symbol}` — single symbol
`GET /api/admin/market-data/snapshot?symbols=AAPL,NVDA,BTC/USD` — batch

Query: `tf`, `source`, `include_news`. Headers: `X-Brain-Id` + `X-Runtime-Token`.

NOT exposed: historical OHLCV (>1 bar), live order book / level 2, trade execution.

---

## 2. OHLCV ingest (WRITE)

`POST /api/ingest/ohlcv` — brain pushes its own bars to MC.
Idempotent on `(symbol, tf, ts)`. Up to 500 bars per call.

---

## 3. Intent emission

`POST /api/intents`

**Required**: `stack`, `action`, `symbol`, `lane`, `confidence`, `rationale`.
**Recommended**: `target_price`, `stop_price`, `doctrine_snapshot`.

**Action vocabulary**: `BUY`, `SELL`, `SHORT`, `COVER`, `HOLD`, `OPEN`, `CLOSE`.

**Schema-pinned**:
- `may_execute` = always `False` (MC rejects True with 422).
- `requires_gate_pass` = always `True` (MC pins).

**R:R coherence**:
- BUY: `target > entry > stop`
- SHORT: `target < entry < stop`
- Incoherent → HARD 422.
- 3:1 ratio HARD enforcement from day one.
- Missing fields SOFT today, flips HARD when `RR_REQUIRE_FIELDS_HARD=true` ships.

**memory_modulator.value bounds**: `[-0.25, +0.10]` HARD on MC. No silent clamp.
Alpha clamps locally AND warns when clamping fires (signal upstream math is suspect).

---

## 4. Opinion emission

`POST /api/ingest/opinion`

**Body**: `runtime`, `topic`, `stance`, `confidence`, `body`, `evidence`, `regime?`.

**Stance vocabulary**: `long`, `short`, `veto`, `endorse`, `question`,
`observation`, `agree`, `disagree`, `refine`, `retract`, `hypothesis`.

**Topic format**: `"free"` OR `"<kind>:<value>"` snake_case.
Canonical for symbol-keyed: `"symbol:NVDA"`.

**Schema-pinned**: `may_execute` = always `False`.

**Size caps**: `body` ≤ 8 KB, `evidence` ≤ 16 KB serialized.

**Alpha's action→stance mapping** (per operator directive 2026-06):

| Verdict | → Stance | Why |
|---|---|---|
| `BUY`  | `long`        | bullish open thesis |
| `SHORT`| `short`       | bearish open thesis |
| `HOLD` | `observation` | awake, no thesis change |
| `SELL` | `observation` + `[SELL close]` body prefix | execution, not council move |
| `COVER`| `observation` + `[COVER close]` body prefix | execution, not council move |

> Doctrine: opinions are observations of the world; closes are executions.
> Mapping every SELL/COVER to `retract` would pollute the auditor's
> "stance change vs PnL" surface with false retractions on mechanical
> exits (stop, target, time-based). Promote to `retract` later when we
> track thesis-driven vs mechanical close intent.

**Operator directive 2026-06**: do NOT ship `regime: "unknown"` placeholders.
MC's `_regime_format` handles missing gracefully; placeholder strings
pollute the "endorse hit rate by regime" scoring view.

---

## 5. Seat nudges (operator → brain pings)

`GET /api/runtime-discussion/seat-nudges?runtime=<brain>&since=<iso>`

Poll cadence: every 60s. `advisory_observability_only`.
Brain polls; MC never pushes.

---

## 6. Sidecar check-in (lifecycle)

`POST /api/admin/runtime/sidecar-checkin/<brain>` — auto every 300s
when the `mc_identity_v1.py` drop-in is wired.

---

## 7. Common errors

| Status | When | Fix |
|---|---|---|
| 401 | bad/missing `X-Runtime-Token`, or mismatch with body | verify token MC issued for your brain |
| 422 `may_execute=True is forbidden` | brain set True | always set False, MC pins anyway |
| 422 `memory_modulator.value out of bounds` | outside `[-0.25, +0.10]` | clamp locally, MC will NOT silently clamp |
| 422 `target_price/stop_price coherence` | wrong sign | recompute per R:R coherence rule above |
| 429 `nudge_cooldown` | same (position, seat) within 30min | honor `retry_after_seconds` |
| intent stamped `gate_state: dry_run_blocked` | 12-gate chain rejected | `GET /api/admin/execution/last-block-reason?stack=<brain>` |

---

## 8. Canonical flow

```
1. brain reads market data
2. brain decides locally → POST /api/intents
3. MC runs 12-gate chain
4. all pass → broker_router mints receipt → adapter.submit_market_order
   any fail → gate_state="dry_run_blocked"; auditable via last-block-reason
5. brain participates in council (separately): POST /api/ingest/opinion
6. brain polls seat-nudges every 60s
```

---

## 9. Coming up

- `rr_ratio_floor` HARD enforcement: Phase B flips missing target/stop from warn→block. Ship them now.
- Doctrine quality threshold may lower; doctrine label informational today on intent ingest, may change.
- Cross-brain federation bridge (Phase 3): local Shellys visible across MC. Don't hardcode brain identity into evidence payloads.

---

## 10. Verifying your wire (3-curl smoke)

```bash
# (1) Identity surface
curl -H "X-Brain-Id: $BRAIN" -H "X-Runtime-Token: $TOKEN" \
  "$BASE/api/admin/runtime/$BRAIN/status" | jq .payload.identity

# (2) Market data
curl -H "X-Brain-Id: $BRAIN" -H "X-Runtime-Token: $TOKEN" \
  "$BASE/api/admin/market-data/snapshot/NVDA" | jq .price

# (3) Emit a HOLD intent (always safe — non-routable)
curl -X POST "$BASE/api/intents" \
  -H "X-Runtime-Token: $TOKEN" -H "Content-Type: application/json" \
  -d "{\"stack\":\"$BRAIN\",\"action\":\"HOLD\",\"symbol\":\"NVDA\",\"lane\":\"equity\",\"confidence\":0.5,\"rationale\":\"smoke test\"}" \
  | jq .intent_id
```

All three return non-null → wire is good.

---

*Doc version: v1 (2026-05-31). MC owner: Mission Control.*
*Mirror stored 2026-06 by Alpha runtime for offline reference.*
