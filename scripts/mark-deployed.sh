#!/usr/bin/env bash
# mark-deployed.sh — snapshot the current "Queued" block in DEPLOYMENT_NOTES.md
# into a timestamped "Shipped" block.
#
# Usage:
#   /app/scripts/mark-deployed.sh "release-2026-02-19-hotfix"
#   /app/scripts/mark-deployed.sh              # auto-label with ISO timestamp
#
# What it does:
#   1. Captures current git HEAD hash and short hash
#   2. Replaces the "↓ No deploys recorded ↓" placeholder (first run) OR
#      inserts a new "Shipped" block above the previous most-recent one
#   3. Moves every entry from "Queued for next deploy" into the new Shipped
#      block, preserving formatting
#   4. Empties the Queued section so future agent changes accumulate cleanly
#
# This is idempotent-ish: running twice in a row without any code changes
# produces a "Shipped" block with no change list (harmless but ugly). Don't
# run it unless you actually deployed.

set -euo pipefail

NOTES_FILE="/app/memory/DEPLOYMENT_NOTES.md"
LABEL="${1:-release-$(date -u +%Y-%m-%dT%H%M%SZ)}"
COMMIT_SHA_FULL=$(git -C /app rev-parse HEAD)
COMMIT_SHA_SHORT=$(git -C /app rev-parse --short HEAD)
NOW_UTC=$(date -u +"%Y-%m-%d %H:%M UTC")

if [[ ! -f "$NOTES_FILE" ]]; then
  echo "ERROR: $NOTES_FILE not found." >&2
  exit 1
fi

# Python does the surgery — much safer than sed on Markdown.
python3 - "$NOTES_FILE" "$LABEL" "$COMMIT_SHA_FULL" "$COMMIT_SHA_SHORT" "$NOW_UTC" <<'PYEOF'
import sys, re, pathlib

path, label, sha, short, now = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
text = pathlib.Path(path).read_text()

# Isolate the Queued block and the Shipped block.
q_re = re.compile(r"(## 🟡 Queued for next deploy\n\n> [^\n]+\n> [^\n]+\n> [^\n]+\n\n)(.*?)(\n---\n\n## 🟢 Shipped to production\n)",
                  re.DOTALL)
m = q_re.search(text)
if not m:
    print("ERROR: Could not locate 'Queued' section in DEPLOYMENT_NOTES.md", file=sys.stderr)
    sys.exit(2)

queued_header, queued_body, shipped_header = m.group(1), m.group(2).strip(), m.group(3)

# Build the new Shipped block.
new_block = (
    f"### {now} — Shipped as `{label}` (`{short}`)\n"
    f"\n"
    f"Commit: `{sha}`\n"
    f"\n"
)
if queued_body:
    new_block += queued_body + "\n\n"
else:
    new_block += "*No queued changes at deploy time.*\n\n"

# Replace placeholder OR insert above existing shipped entries.
placeholder = (
    "### ↓ No deploys recorded via this file yet ↓\n"
    "\n"
    "*(This file was created mid-project. Prior deploys exist but are not\n"
    "catalogued here. The first `mark-deployed` run will create the first\n"
    "\"Shipped\" block and bracket the un-deployed backlog cleanly.)*\n"
)

if placeholder.strip() in text:
    text = text.replace(placeholder.strip(), new_block.rstrip())
else:
    # Insert the new block directly after the "Shipped to production" header
    # and its intro paragraph.
    shipped_intro_re = re.compile(
        r"(## 🟢 Shipped to production\n\n> [^\n]+\n> [^\n]+\n> [^\n]+\n\n)",
        re.DOTALL
    )
    text = shipped_intro_re.sub(lambda mo: mo.group(1) + new_block, text, count=1)

# Clear the queued body — leave the header & blurb intact.
empty_queued = queued_header + "*Nothing queued. Agent will append here as changes land.*\n\n---\n\n"
text = re.sub(
    r"(## 🟡 Queued for next deploy\n\n> [^\n]+\n> [^\n]+\n> [^\n]+\n\n).*?(\n---\n\n## 🟢 Shipped to production\n)",
    lambda mo: empty_queued + "## 🟢 Shipped to production\n",
    text, count=1, flags=re.DOTALL,
)

pathlib.Path(path).write_text(text)
print(f"✓ DEPLOYMENT_NOTES.md updated — '{label}' at {short}")
PYEOF

echo ""
echo "Reminder: commit this change so the journal itself is preserved in git history:"
echo "  git -C /app add memory/DEPLOYMENT_NOTES.md && git -C /app commit -m 'deploy: $LABEL'"
