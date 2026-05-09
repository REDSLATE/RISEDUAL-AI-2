"""Backlog / reference-only feature modules.

────────────────────────────────────────────────────────────────────
NOT IMPORTED ANYWHERE IN THE LIVE CODEBASE.
────────────────────────────────────────────────────────────────────

This package holds feature-engineering proposals that have been
saved for later review but are NOT wired into any production
runtime path:

* No imports from ``services.ml.feature_extraction``
* No imports from ``services.ml.shadow_wiring``
* No imports from any executor / Strategist / Auditor / Shelly /
  Fast Veto / RoadGuard module.
* No retrain script consumes them.

Backlog modules MUST stay self-contained until the operator
explicitly resolves the open questions documented in each module's
header docstring and approves wiring.
"""
