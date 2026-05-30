# Brain identity surface — handoff to Alpha / Camaro / Chevelle / RedEye

Source-of-truth copy of MC's v1 handoff. The Alpha integration lives at:

- Module: `/app/backend/sidecar/mc_identity_v1.py` (verbatim from MC, do not modify)
- Env aliasing: `/app/backend/server.py` startup (`_alias_mc_identity_env_vars`)
- Public route: `GET /api/status` emits the v1 identity block
- Tripwire: `/app/backend/tests/test_mc_identity_v1_contract.py`

Alpha-specific env aliasing — Alpha's deployment already carries
`RISEDUAL_MC_URL`, `ALPHA_MC_INGEST_TOKEN`, `MC_BASE_URL`, and
`MONOREPO_INGEST_TOKEN`. At boot we copy them into the v1-spec names
(`MC_URL`, `MC_INGEST_TOKEN`, `MC_BASE_URL`, `HEARTBEAT_TOKEN`) if
the v1 names aren't already set. This keeps the spec module pristine
while preserving Alpha's historical config.

The spec text below is unchanged from MC's distribution.

---

Mission Control needs each brain to ship an `identity` block on its
`GET /status` endpoint so the operator can see, in one chip on MC's
dashboard, whether the brain's check-in worker is eligible to start.

## Required env vars (per brain deployment)

| Env var            | Purpose                                           |
| ------------------ | ------------------------------------------------- |
| `MC_URL`           | MC base URL — brain → MC periodic check-in target |
| `MC_INGEST_TOKEN`  | Token MC accepts on the check-in path             |
| `MC_BASE_URL`      | MC base URL — brain ← MC opinion-delivery target  |
| `HEARTBEAT_TOKEN`  | Token MC accepts on the opinion / heartbeat path  |

## Lifecycle log contract (must match exactly)

```
INFO mc_checkin worker STARTED — periodic check-in every 300s
     (MC_URL set, MC_INGEST_TOKEN set, MC_BASE_URL set, HEARTBEAT_TOKEN set)
```

```
WARNING mc_checkin worker NOT STARTED — missing env vars: MC_INGEST_TOKEN, HEARTBEAT_TOKEN
```

## Contract pinning

The field set and log format are **v1**. MC's testing agent pins them.
Don't silently change v1 — bump to v2 and dual-publish during a
deprecation window if you have to deviate.
