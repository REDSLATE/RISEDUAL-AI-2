# RISEDUAL AI — repo-level Makefile
#
# Convenience targets for local CI-style guards. Keep targets fast,
# read-only, and side-effect free so they're safe to run from
# git pre-push hooks.

PY ?= python
PYTEST ?= $(PY) -m pytest
BACKEND_DIR := backend

ARCH_TESTS := \
	tests/test_no_local_direction_tuples.py \
	tests/test_code_size.py

# Phase 6 safety bundle. Each line is intentionally a single test
# file so a future move/rename surfaces here as a hard miss instead
# of silently dropping out of the bundle.
#
# Inclusion criteria for this list:
#   * pure-python invariant tests (no Mongo writes, no broker calls)
#   * sub-second runtime per file (verify with `make lint-fast` below)
#   * tests an architectural seal that Phase 6 prep is most likely
#     to brush against (lane isolation, kill-switch, authority,
#     RoadGuard pair, fast-veto pass-through)
FAST_INVARIANT_TESTS := \
	tests/test_dual_stack_invariants.py \
	tests/test_kill_switch.py \
	tests/test_authority_risk_budget.py \
	tests/test_roadguard_pair.py \
	tests/test_roadguard.py \
	tests/test_fast_veto_layer.py \
	tests/test_executor_lanes.py

.PHONY: help lint-arch lint-fast install-pre-push-hook uninstall-pre-push-hook

help:
	@echo "Available targets:"
	@echo "  make lint-arch                 Architecture lint only — direction-tuple"
	@echo "                                 drift + oversized files (~1s)."
	@echo "  make lint-fast                 lint-arch + Phase 6 invariant bundle"
	@echo "                                 (kill-switch, authority, lane separation,"
	@echo "                                 RoadGuard pair, fast-veto, dual-stack)."
	@echo "  make install-pre-push-hook     Optionally install the pre-push hook"
	@echo "                                 (runs lint-arch by default)."
	@echo "  make uninstall-pre-push-hook   Remove the pre-push hook."

# ── Architecture lint ────────────────────────────────────────────
#
# Runs ONLY the two AST-based architecture lint tests:
#   * test_no_local_direction_tuples.py — bans local ("BUY","SELL")
#     literal tuples outside the canonical helper / explicit allowlist.
#   * test_code_size.py — bans source files exceeding the per-file
#     line ceiling without an explicit allowlist entry.
#
# Read-only. NEVER mutates files. Fast enough for a pre-push hook
# (typically <2 seconds even on cold cache).
lint-arch:
	@cd $(BACKEND_DIR) && $(PYTEST) $(ARCH_TESTS) -q

# ── Fast Phase 6 safety bundle ───────────────────────────────────
#
# Superset of lint-arch. Adds the broader architectural-invariant
# tests that Phase 6 prep is most likely to touch:
#   * dual-stack authority invariants
#   * kill-switch coverage
#   * authority + risk-budget gates
#   * RoadGuard pair (lane-isolated equity vs crypto)
#   * Fast Veto pass-through / authority
#   * executor lane separation (no cross-lane bleed)
#
# Same constraints as lint-arch: read-only, no Mongo writes, no
# broker calls, no slow/integration paths. Whole bundle should run
# in well under 5s.
#
# NOT installed into the pre-push hook by default — operator has to
# opt in via `./scripts/install-pre-push-hook.sh --fast`.
lint-fast: lint-arch
	@cd $(BACKEND_DIR) && $(PYTEST) $(FAST_INVARIANT_TESTS) -q

# ── Pre-push hook (opt-in) ───────────────────────────────────────

install-pre-push-hook:
	@./scripts/install-pre-push-hook.sh

uninstall-pre-push-hook:
	@./scripts/install-pre-push-hook.sh --uninstall
