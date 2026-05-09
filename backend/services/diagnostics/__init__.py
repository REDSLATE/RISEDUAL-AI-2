"""Read-only diagnostic / probe modules for offline analysis.

Holds support libraries for the ``scripts/diagnose_*`` family of
read-only diagnostics. NOT runnable on their own — each module
exposes pure helpers + probe coroutines that the corresponding
script in ``backend/scripts/`` orchestrates.

Hard rules
----------
* Read-only. NO writes, NO env mutation, NO broker imports.
* No decision authority. NO calls into the executor / RoadGuard /
  FastVeto / pipeline.
* No artifact writes. No retrain execution.
"""
