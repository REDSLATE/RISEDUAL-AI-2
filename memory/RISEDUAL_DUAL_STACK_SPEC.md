# RISEDUAL Dual-Stack Architecture — Hardened Spec v1.0

> **Frozen 2026-04-28.** Any change to this document requires a version bump
> (e.g., v1.1, v2.0) and a corresponding code review of every module
> declaring `__domain__`. Do not edit historical sections — append.

---

## 1. Core Principle (Non-Negotiable Invariant)

The system is partitioned into two strictly isolated execution domains:

- **Decision-Time Domain (DTD)** — real-time, pre-execution authority
- **Post-Resolution Domain (PRD)** — delayed, post-outcome analytics

> **No component in PRD may mutate, import, call, configure, calibrate, or
> otherwise influence any DTD component except through a registered,
> inactive-by-default Promotion Bridge.**

PRD may *observe* DTD outputs only after settlement.

---

## 2. Decision-Time Stack (DTD)

Latency-bound, deterministic, execution-critical.

```
Strategist + Auditor
    │  (consensus proposal)
    ▼
Adversarial Commander         [Tier-3 gated]
    │  → emits {action, risk_multiplier}
    ▼
Council Risk Modulator        [Tier-3 + ENV gated]
    │  → bounded scaling ∈ [0.50, 1.25]
    ▼
Regime Weights                [per-regime gating]
    │  → bounded scaling ∈ [0.50, 1.25]
    ▼
Order Execution Engine
```

### Hard Guarantees

1. Single-direction flow (top → bottom)
2. No post-trade data access during inference
3. All modulation is bounded, never directional override
4. No HOLD → TRADE promotion allowed
5. Every DTD decision emits a replay record (§7)

---

## 3. Post-Resolution Stack (PRD)

Asynchronous, analytical, non-executing.

```
prediction_tracker
paper_trade_closer
    │ (resolved trades only)
    ▼
firewall.publish_resolved()         [the only DTD → PRD ingress]
    │
    ▼
prd_resolved_outcomes               [append-only ledger]
    │
    ▼
AI Core (live)
    │
    ├─ stats API (rolling metrics)
    ├─ nightly aggregation
    └─ toxic-spike autopsy

AI Core (candidate_vN)
    │
    └─ parallel evaluation (schema-isolated)
```

### Hard Guarantees

1. Operates only on resolved outcomes
2. No access to live execution state
3. No write permissions to DTD systems
4. Schema isolation between engines (live vs candidate)
5. PRD writes flow only to PRD collections

---

## 4. The Firewall

The boundary between DTD and PRD is enforced as a **read-only, delayed,
append-only interface**.

### Rules

1. Data crosses **DTD → PRD only**
2. Crossing occurs **after resolution** (settlement window enforced in code)
3. Interface is **immutable** (no retroactive edits, unique index on outcome_id)
4. **No reverse channel exists by default** — only the Promotion Bridge

### Enforcement Mechanisms (in this order of authority)

1. **Role-scoped DB handles** — `DtdClient`, `PrdReadOnlyClient`,
   `BridgeCalibrationClient` reject cross-domain operations at the
   handle level.
2. **Append-only collections** — `prd_resolved_outcomes` and
   `dtd_decision_replay` have unique indexes; updates raise.
3. **CI invariant tests** — `tests/test_dual_stack_invariants.py` runs on
   every push and asserts every rule in §1–§7.
4. **Explicit bridge registry** — the *only* PRD → DTD path; defaults to
   empty + inactive.

(`__domain__` module declarations remain useful for grep audits but are
not the primary enforcement.)

---

## 5. Promotion Bridge (Inactive by Default)

A Promotion Bridge is the **only** mechanism by which PRD-derived
intelligence may influence DTD.

### Activation Requirements

- Explicit versioned deployment (`bridge_vX`)
- Minimum sample threshold (per regime/asset)
- Out-of-sample validation window
- Regression check vs current production
- Manual approval token (v1: single admin token; v2: multi-sig 2-of-N)

### Behaviour Constraints

- **Injects calibration parameters only** — never raw decisions
- **Cannot override:** action direction, veto/ratify logic
- **Can influence:** thresholds, confidence scaling, risk bounds
  (within pre-set hard limits)
- Every activation persisted to `bridge_activations` (immutable audit log)
- Revocable in one call

---

## 6. Autonomous Evolution Loop (Formalised)

```
Sense → Decide → Execute → Resolve → Measure → Compare → Promote (optional)
└──────  DTD  ─────────────┘  └─────  PRD  ─────────┘    │
                                                          │
                                              Promotion Bridge (gated)
```

| Stage | Domain | Module |
|---|---|---|
| Sense | DTD | data inputs (market_data_pool, headlines, etc.) |
| Decide | DTD | strategist + auditor + commander + council + regime |
| Execute | DTD | broker_service / paper_trader |
| Resolve | DTD→PRD | paper_trade_closer + prediction_tracker (writes via firewall) |
| Measure | PRD | ai_core_engine |
| Compare | PRD | engine registry (live vs candidate) |
| Promote | BRIDGE | promotion_bridge.activate() |

---

## 7. Shadow Replay Channel

Every DTD decision is mirrored into an append-only collection
(`dtd_decision_replay`) for:

- perfect audit trails
- deterministic backtesting
- candidate-engine benchmarking
- legal defensibility

PRD reads this collection through the role-scoped client. PRD cannot
write to it.

---

## 8. Domain Tagging

Every dual-stack-relevant module declares its domain at module level:

```python
__domain__ = "DTD"   # decision-time
__domain__ = "PRD"   # post-resolution
__domain__ = "BRIDGE"  # firewall / promotion bridge / role-scoped clients
```

The CI invariant suite uses these tags to grep-audit for forbidden
cross-domain imports. Modules without a `__domain__` declaration are
considered out-of-scope for the dual-stack rules (utilities, auth,
HTTP plumbing).

---

## 9. IP Notes

- **Patentable claim:** "A dual-domain autonomous decision architecture
  with enforced temporal separation and controlled feedback promotion."
- Cross-domain applicability: robotics, cybersecurity, autonomous
  vehicles, any decision system with delayed ground truth.
- Provisional disclosure draft tracked separately.

---

## 10. Versioning

- **v1.0 (2026-04-28)** — initial frozen spec
- *append future versions here*
