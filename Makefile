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

.PHONY: help lint-arch install-pre-push-hook uninstall-pre-push-hook

help:
	@echo "Available targets:"
	@echo "  make lint-arch                 Run the two architecture lint tests"
	@echo "                                 (direction-tuple drift + oversized files)."
	@echo "  make install-pre-push-hook     Optionally install the pre-push hook."
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

# ── Pre-push hook (opt-in) ───────────────────────────────────────

install-pre-push-hook:
	@./scripts/install-pre-push-hook.sh

uninstall-pre-push-hook:
	@./scripts/install-pre-push-hook.sh --uninstall
