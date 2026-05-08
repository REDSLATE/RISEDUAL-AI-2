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

# Phase 5d/6 read-only governance surface. Each line is a single
# test file so any move/rename surfaces here as a hard miss.
#
# Inclusion criteria:
#   * tests one of the read-only governance surfaces:
#       heartbeat / wedge alerter / artifact inventory /
#       promotion checklist / retrain scaffold
#   * no broker calls, no env mutation, no joblib loading,
#     no backend restart, no promotion actions
#   * sub-second runtime per file (verify with `make lint-safety`)
SAFETY_TESTS := \
	tests/test_phase5d_safety.py \
	tests/test_wedge_alerter.py \
	tests/test_artifact_inventory.py \
	tests/test_promotion_checklist.py \
	tests/test_retrain_alpha_models.py

.PHONY: help lint-arch lint-fast lint-safety install-pre-push-hook uninstall-pre-push-hook

help:
	@echo "Available targets:"
	@echo "  make lint-arch                 Architecture drift check —"
	@echo "                                 direction-tuple drift + oversized files (~1s)."
	@echo "  make lint-fast                 lint-arch + Phase 6 runtime invariant bundle"
	@echo "                                 (kill-switch, authority, lane separation,"
	@echo "                                 RoadGuard pair, fast-veto, dual-stack)."
	@echo "  make lint-safety               Phase 5d/6 governance surface —"
	@echo "                                 heartbeat + wedge alerter + artifact inventory"
	@echo "                                 + promotion checklist + retrain scaffold."
	@echo "                                 Run before any future promotion / post-refactor /"
	@echo "                                 pre-deploy safety review."
	@echo "  make install-pre-push-hook     Optionally install the pre-push hook"
	@echo "                                 (runs lint-arch by default;"
	@echo "                                  see scripts/install-pre-push-hook.sh --help)."
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

# ── Phase 5d/6 governance safety surface ─────────────────────────
#
# Single-command verification of the entire Phase 5d/6 read-only
# safety contract:
#   * stale-model + feature-health + executor heartbeat
#     (test_phase5d_safety.py)
#   * wedge alerter (notification-only paging)
#   * artifact inventory (file-stat + env-read)
#   * promotion checklist (8-check aggregator)
#   * retrain scaffold (artifact-only, dry-run by default)
#
# Same constraints as lint-arch / lint-fast: read-only, no broker
# calls, no env mutation, no joblib loading, no backend restart,
# no promotion actions. Whole bundle runs in ~3s.
#
# Stops at the first failing file via pytest's -x flag and prints
# the failing test node so the operator can see exactly which
# safety layer regressed.
#
# Intended uses:
#   * pre-promotion verification
#   * post-refactor sanity check
#   * pre-deploy safety review
#
# NOT installed into the pre-push hook by default — operator opts
# in via `./scripts/install-pre-push-hook.sh --safety`.
lint-safety:
	@cd $(BACKEND_DIR) && $(PYTEST) -x $(SAFETY_TESTS) -q --no-header || \
		(echo ""; echo "[lint-safety] FAILED in one of:"; \
		 for f in $(SAFETY_TESTS); do echo "    $$f"; done; \
		 exit 1)

# ── Pre-push hook (opt-in) ───────────────────────────────────────

install-pre-push-hook:
	@./scripts/install-pre-push-hook.sh

uninstall-pre-push-hook:
	@./scripts/install-pre-push-hook.sh --uninstall
