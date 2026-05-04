# Tier 3 / Council Activation Playbook

Status: Living operator doc. Keep in sync with code when flags, thresholds, or phases change.

Detailed version: `memory/local/TIER3_ACTIVATION_PLAYBOOK_DETAILED.md` (gitignored).

## Source of Truth

- `backend/services/adversarial_core.py`
- `backend/services/council_tier_gate.py`
- `backend/services/council_risk_modulator.py`
- `backend/services/regime_weights.py`
- `backend/services/research_shadow_stats.py`
- `backend/.env`

## Non-Negotiable Rules

1. Shadow writes only to `research_shadow_decisions`.
2. Shadow must never write to `paper_trades`, `crypto_paper_trades`, `prediction_tracker`, or `trading_bots[].stats`.
3. Tier 3 must never be bypassed by code toggles.
4. Council bounds stay pinned in code: `[0.50, 1.25]`.
5. Council may size-modulate only; it cannot change direction or promote HOLD.
6. Stripe live keys are never touched during rollout.

## Runtime Hierarchy

```txt
Strategist + Auditor
        ↓
Adversarial Commander
shadow → risk_only → veto → full
        ↓
Council Risk Modulator
bounded [0.50, 1.25]
        ↓
Regime Weights
bounded [0.50, 1.25]
        ↓
Order Fill
```

Default state: Commander gated, Council shadow-only, Regime weights inert.

## Flags

### Adversarial

| Flag | Default | Values |
|------|---------|--------|
| `CRYPTO_ADVERSARIAL_ENABLED` | off | `1` or off |
| `CRYPTO_ADVERSARIAL_PHASE` | `shadow` | `shadow`, `risk_only`, `veto`, `full` |

### Shadow

| Flag | Default |
|------|---------|
| `CRYPTO_RESEARCH_SHADOW_ENGINE` | `adversarial` |
| `COUNCIL_SHADOW_MODE` | `rule` |
| `SHADOW_COST_CEILING_USD_PER_DAY` | `5.0` |
| `ML_ADAPTATION_SHADOW_MODE` | `true` |

### Council

| Flag | Default |
|------|---------|
| `COUNCIL_RISK_MODULATOR_ENABLED` | `false` |
| `COUNCIL_MIN_DISSENTS` | `30` |
| `COUNCIL_MIN_WIN_RATE` | `0.55` |
| `COUNCIL_MIN_TOTAL_DELTA_USD` | `0` |

Code-pinned bounds:

- `MAX_COUNCIL_UPWEIGHT=1.25`
- `AGREEMENT_UPWEIGHT=1.10`
- `HIGH_CONFIDENCE=0.70`
- `OPPOSITE_DISAGREE_DOWNWEIGHT=0.50`
- `MIN_COUNCIL_DOWNWEIGHT_FLOOR=0.50`

### Regime Weights

| Flag | Default |
|------|---------|
| `REGIME_WEIGHTS_ENABLED` | `false` |
| `REGIME_MIN_SAMPLES` | `30` |

Bounds: `[0.50, 1.25]`.

## Readiness Endpoint

`GET /api/admin/shadow/tier-readiness`

Council may be enabled only when:

- `tier3_unlocked == true`
- `adversarial_phase == "full"`
- at least one council bucket is open
- `ready_to_enable_council == true`

Companion endpoints:

- `/api/admin/shadow/stats`
- `/api/admin/shadow/regime-stats`
- `/api/admin/shadow/regime-weights`
- `/api/admin/shadow/cost-budget`
- `/api/admin/shadow/cost-history?days=14`
- `/api/admin/shadow/decisions?limit=50&only_dissents=true`
- `/api/admin/shadow/adaptation-shadow-summary?days=14`

## Rollout Phases

| Phase | Action | Promotion Requirement |
|-------|--------|------------------------|
| A | Bake data in shadow | Tier 3 progress 100% and enough scored dissents |
| B | Enable adversarial shadow | Tier 3 unlocked |
| C | `risk_only` | Positive shadow expectancy over 100+ decisions |
| D | `veto` | Risk-only reduces drawdown without killing winners |
| E | `full` | Vetoed trades are negative expectancy |
| F | Council ON | `ready_to_enable_council=true` |
| G | Council LLM shadow | Cost budget is safe |
| H | Regime weights | Regime stats have enough samples |

## Rollback

| Rollback | Set |
|----------|-----|
| Council OFF | `COUNCIL_RISK_MODULATOR_ENABLED=false` |
| Full → veto | `CRYPTO_ADVERSARIAL_PHASE=veto` |
| Veto → risk_only | `CRYPTO_ADVERSARIAL_PHASE=risk_only` |
| Risk_only → shadow | `CRYPTO_ADVERSARIAL_PHASE=shadow` |
| Adversarial OFF | unset `CRYPTO_ADVERSARIAL_ENABLED` |
| LLM → rule | `COUNCIL_SHADOW_MODE=rule` |
| Regime OFF | `REGIME_WEIGHTS_ENABLED=false` |

After changing flags:

```bash
sudo supervisorctl restart backend
sudo supervisorctl status backend
```

## Incident Response

1. Pause bots.
2. Roll back the offending flag.
3. Restart backend.
4. Snapshot:
   - `research_shadow_decisions`
   - `crypto_adversarial_decision_log`
   - `paper_trades`
   - `crypto_paper_trades`
5. Add a regression test before re-enabling.

## Tier 3 Firewall Verification

- Shadow logger writes only to `research_shadow_decisions`.
- Shadow stats read only from `research_shadow_decisions`.
- Tier 3 readiness never reads shadow rows.
- Regression test: `backend/tests/test_shadow_tier3_isolation.py`.

## Quick Check

```bash
curl -s "$REACT_APP_BACKEND_URL/api/admin/shadow/tier-readiness" \
  -H "Authorization: Bearer $TOKEN" | jq '{
    ready: .ready_to_enable_council,
    tier3_pct: .tier3_progress_pct,
    phase: .adversarial_phase,
    modulator_on: .council_modulator_enabled,
    next: .next_steps
  }'
```
