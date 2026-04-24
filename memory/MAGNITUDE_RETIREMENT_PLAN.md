# Magnitude-Path Retirement Plan

## Context

The ML retrain pipeline has TWO parallel severity-weighting paths:

1. **R-multiple path** (preferred) — when a training row carries the
   execution-economics block (`entry_price`, `exit_price`,
   `stop_loss`, `direction`; `schema_version >= 4`), we weight the
   row by realised R-multiple. This is the canonical "signal purity"
   metric because it measures what a trade actually returned
   relative to its planned risk.

2. **Magnitude path** (legacy fallback) — rows missing the
   execution-economics block fall back to a magnitude-based
   severity weight derived from `return_1d` / recent volatility.
   Necessary during the bootstrap era when `features_snapshots`
   rows didn't carry execution economics; a constant source of
   weighting noise now that the schema is v4.

Both paths co-exist today because the live `features_snapshots`
backfill is still catching up — on any given retrain, some rows
have R, some don't. The retrain logs `r_eligible_frac` (fraction
of rows with execution economics) every run. As that number
stabilises, the magnitude path becomes dead weight.

## Retirement Trigger (automated)

The `/api/admin/tier3-progress` endpoint now exposes an
`r_adoption.verdict` block:

```json
{
  "threshold": 0.7,
  "required_consecutive": 3,
  "consecutive_stable_runs": <int>,
  "latest_r_eligible_frac": <float>,
  "magnitude_retirement_ready": <bool>,
  "runs_tracked": <int>
}
```

**We pull the trigger when:**
- `consecutive_stable_runs >= 3` AND
- `latest_r_eligible_frac >= 0.7` AND
- at least 10 runs tracked (to avoid pulling after a 2-row cold
  start)

The admin UI surfaces this as a green "Ready to retire magnitude
path" pill on the ML Health strip (`PaperDaysProgressCard` +
siblings). An amber pill covers the "close but not yet" cases.

## 3-Step Rollout When Trigger Fires

### Step 1 — soft-deprecate magnitude scoring (1 retrain cycle)
- In `services.ml_retrain_service._severity_weights`, gate the
  magnitude branch behind `MAGNITUDE_FALLBACK_ENABLED=true`
  (default ON). Log a warning whenever the branch fires:
  `structured_log("ml.severity.magnitude_fallback", count=<n>,
  frac=<frac>)`.
- Keep the env flag so production can flip it off without a
  redeploy if the switch causes weight distribution drift.

### Step 2 — disable magnitude in retrain, keep code path (1 week)
- Set `MAGNITUDE_FALLBACK_ENABLED=false`. Rows lacking execution
  economics contribute a constant 1.0 weight (no severity signal).
- Monitor three KPIs for one week:
  - `mean_sample_weight` stability (no >15% variance from baseline)
  - Tier 3 win-rate trend (no degradation >3% on 14-day rolling)
  - `r_skipped_frac` (shouldn't spike — indicates stop-outs
    clustering)
- If any KPI drifts, flip the flag back ON and investigate.

### Step 3 — delete the code path (follow-up PR)
- Once Step 2 is green for ≥7 consecutive runs, delete
  `_magnitude_severity_weights` and the env flag. The
  R-path becomes the only severity path. Document the removal
  in `PRD.md` and `CHANGELOG.md`.

## Rollback

The whole flow is gated on `MAGNITUDE_FALLBACK_ENABLED`. If Step
2 triggers a regression, flip the env var, restart the ML
retrain worker. Previous magnitude-weighted rows aren't mutated
(retrain is idempotent — each run rebuilds the weights from
scratch), so rollback is instant and stateless.

## Why not retire immediately?

Two reasons:

1. **R-coverage still < 50% today.** Most `features_snapshots`
   rows from the pre-v4 era don't carry execution economics. If
   we retire magnitude today, 50%+ of rows get a constant weight
   and the model loses severity signal entirely.
2. **Quality gate.** A hasty retirement would silently flatten the
   severity distribution during a volatile week and we might not
   catch it until the next Tier 3 accuracy audit. The verdict
   above locks in "3 consecutive stable runs" so we have evidence
   of stability, not just one good run.

## Status

As of the last audit, `r_eligible_frac` is **0.0** — the live
backfill hasn't produced enough v4 rows yet. The trigger will
not fire until the features_snapshots backfill catches up. Track
progress via `/api/admin/tier3-progress` (admin-only).
