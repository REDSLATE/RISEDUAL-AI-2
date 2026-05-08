#!/usr/bin/env bash
#
# install-pre-push-hook.sh
# Optional installer for the architecture-lint git pre-push hook.
#
# Usage:
#   ./scripts/install-pre-push-hook.sh           # install
#   ./scripts/install-pre-push-hook.sh --uninstall
#
# What it does (install):
#   * Writes .git/hooks/pre-push that calls `make lint-arch`.
#   * Backs up any existing pre-push hook to pre-push.backup.<ts>.
#   * Idempotent — re-running install replaces the managed hook.
#
# What it does NOT do:
#   * Run automatically — installation is opt-in.
#   * Modify behavior code.
#   * Weaken either lint test.
#   * Block pushes if the hook isn't installed.
#
# To bypass once (emergency hotfix):
#   git push --no-verify

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK_PATH="$REPO_ROOT/.git/hooks/pre-push"

# Marker so we recognise our own hook on re-install.
HOOK_MARKER="# risedual-arch-lint-managed-hook"

uninstall() {
    if [ ! -f "$HOOK_PATH" ]; then
        echo "[pre-push] no hook installed — nothing to remove."
        return 0
    fi
    if grep -q "$HOOK_MARKER" "$HOOK_PATH" 2>/dev/null; then
        rm -f "$HOOK_PATH"
        echo "[pre-push] uninstalled managed hook from $HOOK_PATH"
    else
        echo "[pre-push] $HOOK_PATH exists but was not installed by this script."
        echo "          Refusing to remove. Delete manually if intended."
        exit 1
    fi
}

install_hook() {
    if [ ! -d "$REPO_ROOT/.git/hooks" ]; then
        echo "[pre-push] ERROR: $REPO_ROOT/.git/hooks not found — is this a git repo?"
        exit 1
    fi

    # Back up an existing non-managed hook so we never destroy operator state.
    if [ -f "$HOOK_PATH" ] && ! grep -q "$HOOK_MARKER" "$HOOK_PATH" 2>/dev/null; then
        local ts
        ts="$(date -u +%Y%m%dT%H%M%SZ)"
        local backup="${HOOK_PATH}.backup.${ts}"
        cp "$HOOK_PATH" "$backup"
        echo "[pre-push] existing hook backed up → $backup"
    fi

    cat > "$HOOK_PATH" <<'EOF'
#!/usr/bin/env bash
# risedual-arch-lint-managed-hook
#
# Runs the architecture lint guards before every git push.
# Read-only. Fast (<2s typically). Bypass with `git push --no-verify`.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if ! command -v make >/dev/null 2>&1; then
    echo "[pre-push] WARNING: 'make' not on PATH — skipping arch lint."
    exit 0
fi

echo "[pre-push] running 'make lint-arch'…"
if ! make lint-arch; then
    echo
    echo "[pre-push] FAILED. Push blocked by architecture lint."
    echo "[pre-push] Fix the offending file(s) above, or bypass with"
    echo "           git push --no-verify  (use sparingly)."
    exit 1
fi
echo "[pre-push] arch lint passed ✓"
EOF
    chmod +x "$HOOK_PATH"
    echo "[pre-push] installed managed hook at $HOOK_PATH"
    echo "[pre-push] runs 'make lint-arch' on every git push."
    echo "[pre-push] uninstall: ./scripts/install-pre-push-hook.sh --uninstall"
}

case "${1:-install}" in
    --uninstall|-u|uninstall)
        uninstall
        ;;
    --help|-h|help)
        sed -n '2,20p' "$0"
        ;;
    *)
        install_hook
        ;;
esac
