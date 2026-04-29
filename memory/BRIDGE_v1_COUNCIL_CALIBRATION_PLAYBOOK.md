# Bridge Activation Playbook — `bridge_v1_council_calibration`

> **Status as of v1.0:** The first production-targeted Promotion
> Bridge. **Ships INACTIVE by default.** Activation requires the
> evidence below + admin approval token. Revocable in one call.

---

## 1. What this bridge does

Lets PRD-derived calibration nudge the Council Risk Modulator's
output `risk_multiplier` by a multiplicative factor in **[0.90, 1.10]**.

- **Applied AFTER** the Council's existing modulation table (agree
  upweight, opposite-disagree downweight, etc.).
- **Stacked WITH** the Council's bounds — the Council still clamps
  to [0.50, 1.25] internally; the bridge then nudges within ±10%.
- **Cannot influence** action direction, `council_applied` flag,
  or the `reason` tag. Only the final `risk_multiplier` value.

When inactive, `get_calibration("bridge_v1_council_calibration")`
returns `None` and the Council's output is **byte-identical** to the
pre-bridge contract (regression test
`test_modulator_unchanged_when_no_bridge_active` enforces this).

---

## 2. Activation prerequisites

| Requirement | Threshold | Where to find evidence |
|---|---|---|
| Sample count of resolved trades the calibration value was derived from | **≥ 200** | `/api/ai-core/engines/{name}/stats` → `total_resolved` |
| Out-of-sample window | **≥ 14 days** | Operator commitment — track in metadata |
| Regression % vs current production | **≤ 2.0%** | Backtest comparison report |
| Admin role | required | Owner/Admin only |
| `BRIDGE_APPROVAL_TOKEN` env match | required | Set the env in `/app/backend/.env`, restart backend, send token in request body |
| Value within bounds | `0.90 ≤ value ≤ 1.10` | Hard clamp in code |

---

## 3. Activation flow

### 3a. Set the approval token (one-time)

```bash
# Backend .env, then `sudo supervisorctl restart backend`
BRIDGE_APPROVAL_TOKEN=<a-secret-string-known-only-to-the-operator>
```

### 3b. POST the activation

```bash
curl -X POST "${REACT_APP_BACKEND_URL}/api/admin/bridges/bridge_v1_council_calibration/activate" \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "value": 1.05,
    "approval_token": "<the-secret-from-3a>",
    "evidence": {
      "sample_count": 320,
      "oos_window_days": 21,
      "regression_pct": 0.8,
      "calibration_source": "candidate_v2 confidence_x_agent",
      "reviewed_by": "operator@example.com"
    }
  }'
```

**Response (success):**
```json
{"ok": true, "active": true, "name": "bridge_v1_council_calibration", "value": 1.05}
```

**Refusals (any of):**
- `invalid_approval_token` — env unset or doesn't match request body
- `insufficient_samples:N<200`
- `insufficient_oos:N<14`
- `regression_above_threshold:N>2.0`
- `value_out_of_bounds:value∉[0.9,1.1]`

### 3c. Verify the activation took effect

```bash
curl "${REACT_APP_BACKEND_URL}/api/admin/bridges" -H "Authorization: Bearer <admin-token>"
```

The bridge should now appear in the `active` map. Council's next
modulation call will include a `bridge_calibration` field in the
result dict.

---

## 4. Revocation

```bash
curl -X POST "${REACT_APP_BACKEND_URL}/api/admin/bridges/bridge_v1_council_calibration/revoke" \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"reason": "regression detected on 2026-05-10 daily review"}'
```

Idempotent. Returns `{"ok": true, "active": false, "name": "bridge_v1_council_calibration"}`.
The Council immediately reverts to pre-bridge behaviour on the very
next decision (no restart required — the calibration is read from
in-memory `_ACTIVE` on every invocation).

---

## 5. Audit log

```bash
curl "${REACT_APP_BACKEND_URL}/api/admin/bridges/audit?limit=50" -H "Authorization: Bearer <admin-token>"
```

Returns an immutable timeline: every activate / revoke event with
actor, timestamp, value, evidence snapshot, and reason. Stored in
`bridge_activations` (BRIDGE-domain collection — DTD cannot read).

---

## 6. Hard rules (cannot be relaxed without spec version bump)

1. The bridge can never set `risk_multiplier` to **zero**, **negative**,
   or **opposite-direction**. Bounds [0.90, 1.10] guarantee
   strictly-positive multiplicative nudges.
2. The bridge cannot enable trading on a HOLD — `commander_action ==
   "HOLD"` returns the unchanged base multiplier, and even then the
   bridge nudge is multiplicative on a HOLD pass-through (which has
   `council_applied: False`, so position-sizing code treats it as
   advisory at best).
3. The bridge does NOT short-circuit the Council's existing 0.5×
   floor or 1.25× ceiling. Those bounds run BEFORE the bridge nudge.
4. The bridge cannot persist across a backend restart unless the
   activation is re-issued. By design — operator must re-affirm
   evidence after any production deploy or rollback.

---

## 7. Versioning

- **v1 (this spec)** — initial bridge.
- **v2 (planned)** — multi-sig 2-of-N approval token.
- **v3 (planned)** — auto-revoke on regression-detector trigger.

Any change to bounds, sample thresholds, or output target requires a
new bridge name (e.g. `bridge_v2_council_calibration`) — existing
activations of the v1 bridge are unaffected.
