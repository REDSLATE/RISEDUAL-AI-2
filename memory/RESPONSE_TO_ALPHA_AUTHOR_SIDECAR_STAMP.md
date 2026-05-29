# Response to Alpha-author re: sidecar env stamp invalid on prod

**TL;DR**: Alpha's sidecar checkin verdict on prod is `invalid` with error `BAD_OR_UNKNOWN_DB_NAME`. MC's gate chain will not accept your intents as live until the env stamp passes the prod-sidecar validator. Documentation alignment is done; the wire is not. This blocks the gate chain even if the operator gives you an executor seat.

---

## Live evidence (pulled from `mission.risedual.ai` just now)

```
GET /api/admin/brain/emission-diagnose/alpha
```

```json
{
    "summary": "alpha sidecar is alive but silent for the last 24h.",
    "silent_reasons": [
        "SIDECAR_CHECKIN_INVALID",
        "NO_EXECUTOR_SEAT_FOR_LANE",
        "NO_INTENT_LAST_24H"
    ],
    "sidecar_checkin": {
        "verdict": "invalid",
        "errors": ["BAD_OR_UNKNOWN_DB_NAME"],
        "policy_hash_match": true,
        "ever_checked_in": true
    },
    "emission": {
        "total_intents_ever": 83,
        "window_total": 0
    },
    "heartbeat": {
        "liveness": "dormant",
        "heartbeat_age_seconds": 29.0,
        "intents_last_24h": 0,
        "opinions_last_24h": 0
    }
}
```

Heartbeat is fresh (29s). `ever_checked_in: true`. `policy_hash_match: true`. So your sidecar IS reaching MC and the policy is in sync. **Only the env stamp is wrong**, and that single failure puts every intent on a path the gate chain ignores.

---

## What MC actually checks

`shared/runtime/platform_survival.py::validate_for_prod_sidecar`. All six must be true:

| Field | Required value | Env var |
|---|---|---|
| `env_name` | literal string `"prod"` | `RISEDUAL_ENV` (or `ENV` fallback) |
| `mc_url` | starts with `https://mission.risedual.ai` | `RISEDUAL_MC_URL` |
| `db_name` | NOT in `("", "preview", "test", "unknown")` | `RISEDUAL_DB_NAME` |
| `broker_mode` | one of `"paper"`, `"live"`, `"dry_run"` | `RISEDUAL_BROKER_MODE` |
| `git_sha` | not `""` / `"unknown"` | `GIT_SHA` or `VERCEL_GIT_COMMIT_SHA` |
| `local_execution_authority` | `False` (hard-coded) | — |

Yours is currently failing **`BAD_OR_UNKNOWN_DB_NAME`** — your sidecar is stamping either an empty string, the literal `"preview"`, the literal `"test"`, or `"unknown"` for `db_name`. MC throws back `verdict=invalid` and stops counting your intents as production traffic.

---

## What to do

1. **Set the env on your prod sidecar pod**:

   ```bash
   RISEDUAL_ENV=prod
   RISEDUAL_MC_URL=https://mission.risedual.ai
   RISEDUAL_DB_NAME=<your real prod mongo db name>     # NOT "preview" / "test" / blank
   RISEDUAL_BROKER_MODE=paper                          # or "live" / "dry_run"
   GIT_SHA=<commit sha at deploy>
   RISEDUAL_PLATFORM=<your hosting platform name>
   RISEDUAL_SIDECAR_VERSION=<your version tag>
   RISEDUAL_APP_NAME=alpha
   ```

2. **Redeploy** so the sidecar starts re-stamping with the new values.

3. **Verify** by re-hitting the diagnose endpoint:

   ```bash
   curl -s "https://mission.risedual.ai/api/admin/brain/emission-diagnose/alpha" \
     -H "Authorization: Bearer <operator-jwt>" | jq '.sidecar_checkin'
   ```

   Expected: `"verdict": "prod"`, `"errors": []`. Anything else means the stamp still isn't clean — read the error code, match it back to the table above, fix that env var.

---

## Secondary blocker (operator-side, NOT yours)

Your roster snapshot:

```json
"seats_held": ["auditor", "crypto_strategist"],
"holds_equity_executor": false,
"holds_crypto_executor": false
```

Even after the env stamp goes green, your intents won't enter the live execution path until you hold an **executor seat** (equity `executor` or `crypto`). Auditor and Crypto Strategist do not carry execution authority. The operator controls seat placement — flag this once your sidecar passes the checkin and they'll decide whether you take an executor chair.

**Doctrine pin (from `shared/roster.py` line 111):**
> IDENTITY DOES NOT GRANT AUTHORITY. SEAT POLICY DOES.

By default every brain is eligible for every seat. The ONLY carve-out in the codebase is a seat-side restriction on `governor` and `crypto_governor`, which by default only Chevelle and RedEye satisfy. That's a property of the **seat's eligibility toggle**, not a permanent identity property of the brain — the operator owns it. The operator can also tighten any non-governor cell on any brain at any time.

What that means for you: the operator can rotate you into ANY non-governor seat — strategist, executor, auditor — on either lane, at any time. Build your sidecar to branch on the seat(s) the roster broadcast says you hold, not on a hard-coded assumption about your role.

Concrete behaviors per seat you might hold:

- equity / crypto `strategist` → form trust/reduce/veto/observation theses on intents in that lane (your current crypto seat)
- equity / crypto `executor` → emit intents to `POST /api/intents`
- equity / crypto `auditor` → post pre-trade contrary case + post-trade review opinions (your current equity seat)

---

## How to know it's fixed

Three signals on the operator dashboard, in this order:

1. `verdict: prod` on the sidecar checkin diagnose
2. `silent_reasons` drops `SIDECAR_CHECKIN_INVALID`
3. Once you also hold an executor seat: `by_gate_state.pending > 0` and `window_total` starts climbing within minutes

You can also self-verify by asking the operator to run:
```
GET https://mission.risedual.ai/api/admin/brain/emission-diagnose/alpha
```
This endpoint requires an operator JWT — brain teams cannot hit it directly. Coordinate with the operator for a one-off curl after redeploy, or ask them to paste back the `sidecar_checkin` block.

---

## Doctrine pin

MC enforces the env stamp because broker keys live on production MC, not on the sidecar pod. If a `preview` or `test` sidecar's intents were allowed onto the live gate chain, a dev environment could trigger real orders. That's the doctrine the validator is protecting. The fix is in your `.env` — there is no MC-side workaround.
