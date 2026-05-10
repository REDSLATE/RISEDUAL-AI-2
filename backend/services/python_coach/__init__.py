"""Python Coach — operator-facing learning surface for Alpha.

DOCTRINE
--------
* The coach AUDITS Python code statically (AST). It NEVER executes
  user-supplied code (no exec / eval / subprocess).
* The coach generates lesson plans via the existing ``ai_service``
  router (Emergent LLM key, no new integration).
* The coach has ZERO authority over the codebase — it cannot import
  ``services.code_evolution``, ``services.broker``, or any
  execution path. Tests pin this.
* Operator-only at the API layer (role == "owner").
"""
