"""Code Evolution — RISEDUAL's self-review layer for code patches.

DOCTRINE
--------
* AI may audit code.
* AI may recommend tests.
* AI may write receipts.
* AI may NOT run shell commands.
* AI may NOT promote code.
* AI may NOT modify its own gate.

This package implements the v0 review pipeline: AST invariants →
risk classification → Mongo receipt → operator countersign. There
is intentionally no subprocess test runner in v0 — tests run
under operator authority only.
"""
