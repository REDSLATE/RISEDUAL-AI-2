# Tier 3 / Council Activation Playbook

**Audience:** Operators and future agents handling the rollout of the Adversarial Core, the Council Risk Modulator, and Regime-Conditional Weights.

**Status:** Living document. Update whenever a flag default, threshold, or phase rule changes in code.

**Source-of-truth files** (changes here MUST stay in sync with this doc):
- `/app/backend/services/adversarial_core.py` — phase/env constants
- `/app/backend/services/council_tier_gate.py` — Council promotion thresholds
- `/app/backend/services/council_risk_modulator.py` — bounded modulation table
- `/app/backend/services/regime_weights.py` — regime weight bounds
- `/app/backend/services/research_shadow_stats.py::fetch_tier_readiness` — readiness payload
- `/app/backend/.env` — current flag values

---

## 1. Hard Rules — DO NOT VIOLATE

These are non-negotiable. Breaking any one of them invalidates the entire safety design.

| # | Rule | Why |
|---|------|-----|
| 1 | **Never write to `paper_trades`, `crypto_paper_trades`, `prediction_tracker`, or `trading_bots[].stats` from the shadow layer.** Shadow logger writes ONLY to `research_shadow_decisions`. | Tier 3 gate reads `crypto_adversarial_decision_log` + prediction tracker — shadow data poisoning would auto-unlock Tier 3 with fake evidence. |
| 2 | **Never bypass the Tier 3 gate by toggling code.** The gate is read at runtime from `services.tier3_readiness.check_tier3_unlock`. If you need to test Tier 3 behaviour, seed `crypto_adversarial_decision_log` rows in a non-prod DB. | Tier 3 is the last guardrail between "model says go" and "real money moves." |
| 3 | **Never raise the modulator bounds via env.** `MAX_COUNCIL_UPWEIGHT=1.25` and `MIN_COUNCIL_DOWNWEIGHT_FLOOR=0.50` are pinned in code. To change them, do a code review + redeploy. | Operator-induced misconfigs cannot unlock 2× sizing. |
| 4 | **Never let Council change direction or promote a HOLD.** The modulator is a multiplier on Commander's existing `risk_multiplier`. Period. | Hierarchy: Adversarial owns the fill decision; Council only modulates size. |
| 5 | **Never touch Stripe live keys.** Subscriptions are wired in LIVE mode. | Production revenue path. |

---

## 2. Engine Hierarchy (one-screen mental model)

```
                ┌─────────────────────────────────────┐
                │          Strategist + Auditor       │  ← always live (consensus)
                └────────────────┬────────────────────┘
                                 │ proposes trade
                                 ▼
        ┌────────────────────────────────────────────┐
        │       Adversarial Commander (Bull/Bear)     │  ← gated by Tier 3 + ENV
        │   phases: shadow → risk_only → veto → full  │
        └────────────────┬───────────────────────────┘
                         │ emits {action, risk_multiplier}
                         ▼
        ┌────────────────────────────────────────────┐
        │        Council Risk Modulator (LLM v2)      │  ← gated by Tier 3 + ENV +
        │      bounded multiplier ∈ [0.50, 1.25]      │     per-bucket data gate
        └────────────────┬───────────────────────────┘
                         │ final risk_multiplier
                         ▼
        ┌────────────────────────────────────────────┐
        │       Regime Weights (asset_type × regime)  │  ← gated by ENV +
        │      bounded multiplier ∈ [0.50, 1.25]      │     per-regime data gate
        └────────────────┬───────────────────────────┘
                         │ final position size
                         ▼
                     Order Fill
```

**Default state (today):** Commander silent (Tier 3 locked). Council shadow-only. Regime weights default-inert (`weight=1.0` everywhere).

---

## 3. Env Flag Inventory

All flags live in `/app/backend/.env`. After any change run:
```bash
sudo supervisorctl restart backend
```

### 3.1 Adversarial Core

| Flag | Default | Purpose | Valid values |
|------|---------|---------|--------------|
| `CRYPTO_ADVERSARIAL_ENABLED` | _(unset → off)_ | Final ops switch on the Adversarial layer. Even when Tier 3 unlocks, this must be `1` for any Bull/Bear/Commander code to run. | `1` (on) or anything else (off) |
| `CRYPTO_ADVERSARIAL_PHASE` | `shadow` | Per-cycle behaviour once both gates open. | `shadow`, `risk_only`, `veto`, `full` |

Phase semantics (from `adversarial_core.py` lines 41-57):

| Phase | Behaviour | When to promote |
|-------|-----------|-----------------|
| `shadow` | Log every Commander decision to `crypto_adversarial_decision_log`. Do NOT alter the live fill. | After ≥50–100 logged decisions exist for the bot bucket. |
| `risk_only` | Caller multiplies position size by `payload.risk_multiplier ∈ [0,1]`. Direction unchanged. | After `shadow` shows positive expectancy (counterfactual P&L > 0) over ≥100 decisions. |
| `veto` | If Commander returns `decision="NO_TRADE"`, skip the fill entirely. | After `risk_only` proves the risk multiplier reduces drawdown without killing winners. |
| `full` | Commander's `decision` (`LONG` / `SHORT_OR_AVOID`) overrides Strategist+Auditor direction. | After ≥30 dissenting decisions in `veto` phase show the Commander would have fired better trades than the consensus did. **Earned, not granted.** |

### 3.2 Research Shadow Layer

| Flag | Default | Purpose |
|------|---------|---------|
| `CRYPTO_RESEARCH_SHADOW_ENGINE` | `adversarial` | Which alternative engine the shadow logger runs in parallel. Values: `none`, `adversarial`, `council`. |
| `COUNCIL_SHADOW_MODE` | `rule` | When `CRYPTO_RESEARCH_SHADOW_ENGINE=council`, picks the consensus implementation. `rule` = deterministic 3-rule majority. `llm` = real Emergent-LLM 3-model consensus (incurs cost). |
| `SHADOW_COST_CEILING_USD_PER_DAY` | `5.0` | Daily LLM spend ceiling for Council shadow runs (per-bot rolling 24h). Above this the cost-budget endpoint flips bots to `degraded` then `paused`. |
| `ML_ADAPTATION_SHADOW_MODE` | `true` | When `true`, the auto-revert rail evaluates every cycle but only logs (no real adaptations applied). Lets us verify the rail's decisions before letting it act on its own. |

### 3.3 Council Risk Modulator

| Flag | Default | Purpose |
|------|---------|---------|
| `COUNCIL_RISK_MODULATOR_ENABLED` | `false` | Master switch. Even with all data gates green, the modulator is inert until this is `true`. |
| `COUNCIL_MIN_DISSENTS` | `30` | Minimum scored dissents in a single `(council, asset_type)` bucket before that bucket's data gate can open. |
| `COUNCIL_MIN_WIN_RATE` | `0.55` | Disagreement-conditional win-rate floor. |
| `COUNCIL_MIN_TOTAL_DELTA_USD` | `0` | Counterfactual P&L floor (sum of per-dissent simulated δ$). Must be `> 0` to clear. |

**Code-pinned bounds (NOT env-overridable):**
- `MAX_COUNCIL_UPWEIGHT = 1.25`
- `AGREEMENT_UPWEIGHT = 1.10`
- `HIGH_CONFIDENCE = 0.70`
- `OPPOSITE_DISAGREE_DOWNWEIGHT = 0.50`
- `MIN_COUNCIL_DOWNWEIGHT_FLOOR = 0.50`

### 3.4 Regime Weights

| Flag | Default | Purpose |
|------|---------|---------|
| `REGIME_WEIGHTS_ENABLED` | `false` | Master switch. Off → `lookup_weight` returns `1.0` always. |
| `REGIME_MIN_SAMPLES` | `30` | Per-regime scored-dissent floor before that regime's weight may deviate from `1.0`. |

**Code-pinned bounds:** `[0.50, 1.25]` clamp + win-rate envelope (0.65→1.20×, 0.55→1.10×, 0.45→1.00×, 0.35→0.85×, <0.35→0.70×).

> **Note:** The regime-weights lookup hook is intentionally NOT yet wired into the bot scheduler. Activation requires steps 1–3 in `regime_weights.py` doc-comment + a code change.

---

## 4. The Readiness Endpoint

```http
GET /api/admin/shadow/tier-readiness
Authorization: Bearer <admin/owner JWT>
```

Returns a single JSON payload aggregating all four gates. **Read-only — no flip button.** Operators flip flags via `.env` edit + supervisorctl restart.

### 4.1 Response shape

```json
{
  "council_modulator_enabled": false,
  "adversarial_phase": "shadow",
  "tier3_progress_pct": 42.5,
  "tier3_unlocked": false,
  "tier3_blockers": ["needs more closed predictions", "..."],
  "ready_to_enable_council": false,
  "council_buckets": [
    {
      "engine": "council",
      "asset_type": "crypto",
      "open": false,
      "dissent_count": 14,
      "needed_dissents": 16,
      "win_rate": 0.58,
      "total_delta_usd": 2.31,
      "reason": "needs 16 more dissents"
    }
  ],
  "next_steps": [
    "Tier 3 not yet unlocked (42.5/100). Blockers: ...",
    "Adversarial phase is 'shadow', needs to reach 'full' before Council can ride along",
    "No Council bucket has cleared all 3 thresholds yet"
  ]
}
```

### 4.2 Field interpretation

- **`ready_to_enable_council`** is `true` ONLY when ALL three are met: Tier 3 unlocked, Adversarial phase = `full`, ≥1 Council bucket open.
- **`council_buckets[].reason`** picks the single most-blocking unmet threshold so the operator sees one clear next step (not three competing complaints).
- **`next_steps`** is the operator-readable checklist. When all gates are green and the modulator is still off, you'll see: `"All gates green — set COUNCIL_RISK_MODULATOR_ENABLED=true in .env and restart backend"`.

### 4.3 Companion endpoints

- `GET /api/admin/shadow/stats` — disagreement-conditional win-rate per `(engine, asset_type)` bucket.
- `GET /api/admin/shadow/regime-stats` — per-regime dissent stats (raw input to regime weights).
- `GET /api/admin/shadow/regime-weights` — computed weights (default-inert until samples mature).
- `GET /api/admin/shadow/cost-budget` — per-bot rolling 24h LLM spend.
- `GET /api/admin/shadow/cost-history?days=14` — daily LLM spend sparkline data.
- `GET /api/admin/shadow/decisions?limit=50&only_dissents=true` — raw per-cycle feed.
- `GET /api/admin/shadow/adaptation-shadow-summary?days=14` — what the auto-revert rail WOULD have done.

---

## 5. Rollout Sequence

> Each step has explicit go/no-go criteria. **Do not skip a step.** The whole system is designed to fail safe (every gate defaults closed); skipping a step does not break safety, but it skips the empirical proof that lets you trust the next step.

### Phase A — Bake the data (current state, no operator action)

1. Strategist + Auditor run live as today.
2. Shadow layer runs Council in `rule` mode against every cycle, logging dissents.
3. Adversarial Commander runs **internally** but is gated off (Tier 3 locked + `CRYPTO_ADVERSARIAL_ENABLED` unset). Dissents accumulate.
4. APScheduler runs the deferred tactical/strategic scorer every N minutes, attaching counterfactual P&L to closed shadow rows.

**Go/no-go:** Wait until `tier3_progress_pct ≥ 100` AND ≥30 scored dissents per `(council, crypto)` bucket. Verify via `GET /api/admin/shadow/tier-readiness`.

### Phase B — Open the Adversarial layer in shadow

1. Verify `tier3_unlocked: true` in the readiness payload.
2. In `/app/backend/.env`:
   ```
   CRYPTO_ADVERSARIAL_ENABLED=1
   CRYPTO_ADVERSARIAL_PHASE=shadow
   ```
3. `sudo supervisorctl restart backend`
4. Verify `crypto_adversarial_decision_log` is now receiving rows.
5. Verify `paper_trades` / `prediction_tracker` show **no behaviour change**.

**Go/no-go:** ≥100 logged Adversarial decisions, counterfactual P&L > 0, and `dissent_rate` against Strategist+Auditor sits between 5–35% (lower means agent has nothing to add; higher means it disagrees too often to trust yet).

### Phase C — Adversarial: `risk_only`

1. Set `CRYPTO_ADVERSARIAL_PHASE=risk_only` and restart backend.
2. The bot now multiplies position size by `payload.risk_multiplier ∈ [0,1]` per cycle. Direction unchanged.
3. Watch P&L vs. shadow-baseline for at least one week of paper trading.

**Go/no-go:** Drawdown at the bot level should **decrease** (or hold flat) without corresponding decrease in winning trade count. If win-rate drops by >5% vs. shadow baseline, **rollback** to `shadow` and investigate.

### Phase D — Adversarial: `veto`

1. Set `CRYPTO_ADVERSARIAL_PHASE=veto` and restart backend.
2. Commander can now block trades by returning `decision="NO_TRADE"`. Direction still cannot be overridden.
3. Watch for ≥2 weeks across multiple regimes.

**Go/no-go:** Vetoed trades, evaluated counterfactually 30 minutes forward, should have negative expected P&L on average. If vetoed trades would have been winners, **rollback** to `risk_only`.

### Phase E — Adversarial: `full`

1. Set `CRYPTO_ADVERSARIAL_PHASE=full` and restart backend.
2. Commander can now override the Strategist+Auditor direction.
3. Review `/api/admin/shadow/tier-readiness` daily.

**Go/no-go for promotion to Phase F:** `adversarial_phase: "full"` AND ≥1 Council bucket open in `tier-readiness`.

### Phase F — Council Risk Modulator: ON

1. Confirm readiness payload shows `ready_to_enable_council: true`.
2. In `/app/backend/.env`:
   ```
   COUNCIL_RISK_MODULATOR_ENABLED=true
   ```
3. `sudo supervisorctl restart backend`
4. Verify in the next bot cycle that Council results show `council_applied: true` for at least some agreement and high-confidence-disagreement events.
5. Hard-bound check: log inspection should show **no** `risk_multiplier` outside `[0.50, 1.25]`.

The active modulation table (from `council_risk_modulator.py` lines 20-28):

| Council says | Modulation | Floor / ceiling |
|---|---|---|
| (modulator off) | no change | — |
| (tier closed) | no change | — |
| Commander HOLD | no change | — (cannot promote) |
| Same direction | × 1.10 | cap at 1.25 |
| Opposite + ≥0.70 conf | × 0.50 | floor at 0.50 |
| Opposite + <0.70 conf | no change | — |
| HOLD vs LONG | no change | — |

### Phase G — Council Shadow → LLM mode

> Independent of Phase F. Lets you spend real LLM dollars on Council shadow runs once you're confident the rule-based version was directionally accurate.

1. Inspect the cost sparkline (`/cost-history`) for projected daily cost under `llm` mode.
2. Confirm `SHADOW_COST_CEILING_USD_PER_DAY` is set conservatively (default `5.0`).
3. Set `COUNCIL_SHADOW_MODE=llm` and restart backend.
4. Check `/api/admin/shadow/cost-budget` 24h later — bots should be `full` tier (not `degraded` / `paused`).

### Phase H — Regime-Conditional Weights

> Independent of Phases F/G. Activates per-regime sizing.

1. Verify `GET /api/admin/shadow/regime-weights` shows ≥1 actionable regime per asset type.
2. Wire the scheduler hook (currently NOT wired). The 5-line patch lives in the bot scheduler — call `lookup_weight(payload, asset_type=..., regime=...)` and multiply position size by the result.
3. Set `REGIME_WEIGHTS_ENABLED=true` and restart backend.

---

## 6. Rollback Procedure

Every phase has the same one-step rollback: **set the flag back, restart, watch.**

| To rollback | Set | Effect |
|---|---|---|
| Council Modulator | `COUNCIL_RISK_MODULATOR_ENABLED=false` | Modulator returns no-op. Commander's risk_multiplier passes through unchanged. |
| Adversarial `full` → `veto` | `CRYPTO_ADVERSARIAL_PHASE=veto` | Direction overrides stop; vetoes still active. |
| Adversarial `veto` → `risk_only` | `CRYPTO_ADVERSARIAL_PHASE=risk_only` | NO_TRADE returns no longer block fills. |
| Adversarial `risk_only` → `shadow` | `CRYPTO_ADVERSARIAL_PHASE=shadow` | Risk multiplier no longer applied. Logging only. |
| Adversarial OFF | `CRYPTO_ADVERSARIAL_ENABLED=` _(unset)_ | Whole layer silent. Zero work per cycle. |
| Council LLM → rule | `COUNCIL_SHADOW_MODE=rule` | Shadow Council reverts to deterministic majority. LLM cost stops. |
| Regime Weights OFF | `REGIME_WEIGHTS_ENABLED=false` | `lookup_weight` returns `1.0` always. |

After every rollback:
```bash
sudo supervisorctl restart backend
sudo supervisorctl status backend
curl -s "$REACT_APP_BACKEND_URL/api/admin/shadow/tier-readiness" -H "Authorization: Bearer <admin token>" | jq .
```
Confirm the readiness payload now reflects the rolled-back state before walking away.

---

## 7. Misfire / Incident Response

If a phase causes a real-money incident, immediately:

1. **Pause all bots** via the existing `/api/admin/bots/{id}/pause` endpoint (or the global kill-switch in the AdminPanel).
2. **Roll back** the offending flag using the table in §6.
3. **Restart backend.**
4. **Snapshot** the relevant Mongo collections for postmortem:
   - `research_shadow_decisions` (scoped to the affected bot/window)
   - `crypto_adversarial_decision_log` (same)
   - `paper_trades` / `crypto_paper_trades` for the actual fills
5. **Diff** the rolled-back `next_steps` against the pre-incident `next_steps` to confirm the flag did revert.
6. **Investigate before re-enabling.** Do not promote the flag again until the cause is identified and a regression test is added under `/app/backend/tests/`.

---

## 8. Tier 3 Firewall — How We Verify It

The firewall is enforced by code paths, not configuration. Verification checklist:

- `services/research_shadow_logger.py` writes ONLY to `research_shadow_decisions`.
- `services/research_shadow_stats.py` reads ONLY from `research_shadow_decisions`.
- The Tier 3 readiness check (`services/tier3_readiness.py`) reads from `crypto_adversarial_decision_log` and `prediction_tracker` — **never** from `research_shadow_decisions`.
- Regression test: `/app/backend/tests/test_shadow_tier3_isolation.py` asserts that adding shadow rows does NOT change the Tier 3 score.

If you ever find a code path that violates this isolation, **stop** and fix it before touching any flag in §3.

---

## 9. Quick Reference Card

```bash
# Read the gate state
curl -s "$REACT_APP_BACKEND_URL/api/admin/shadow/tier-readiness" \
  -H "Authorization: Bearer $TOKEN" | jq '{
    ready: .ready_to_enable_council,
    tier3_pct: .tier3_progress_pct,
    phase: .adversarial_phase,
    modulator_on: .council_modulator_enabled,
    next: .next_steps
  }'

# Promote Adversarial phase (after meeting go/no-go)
sed -i 's/^CRYPTO_ADVERSARIAL_PHASE=.*/CRYPTO_ADVERSARIAL_PHASE=risk_only/' /app/backend/.env
sudo supervisorctl restart backend

# Enable the Council Modulator (after ready_to_enable_council=true)
sed -i 's/^COUNCIL_RISK_MODULATOR_ENABLED=.*/COUNCIL_RISK_MODULATOR_ENABLED=true/' /app/backend/.env
sudo supervisorctl restart backend

# Emergency rollback (Adversarial OFF)
sed -i 's/^CRYPTO_ADVERSARIAL_ENABLED=.*/CRYPTO_ADVERSARIAL_ENABLED=/' /app/backend/.env
sudo supervisorctl restart backend
```

---

## 10. Document Maintenance

- **Owner:** main agent / lead operator.
- **Review cadence:** every time a flag default, threshold, or phase rule is changed in code, update §3 and §5 in the same PR.
- **Cross-references:** `/app/memory/PRD.md`, `/app/memory/ROADMAP.md`, `/app/memory/CHANGELOG.md`, `/app/memory/DEPLOYMENT_NOTES.md`.

_Last updated: Feb 2026._
