"""Alpha Knowledge Base — read-only Python corpus the runtime can consult.

DOCTRINE
--------
* This KB is read-only memory. Alpha may consult it; Alpha may NOT
  use it as a code-generation source for the patch-promotion path.
* The KB CANNOT be imported by ``services.code_evolution`` or any
  execution path. Tests pin both directions.
* All chunks carry ``excluded_from_code_gate_inputs=True`` — a future
  patch-risk classifier MUST filter on this flag.
* Paper-trading-only — the user explicitly scoped this corpus for
  use in a no-live-execution environment.
"""
