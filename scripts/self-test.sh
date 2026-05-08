#!/usr/bin/env bash
# RISEDUAL AI — Self-Test CLI
#
# Run before every deploy. Exits 0 on PASS, 1 on FAIL.
#
# Usage:
#   /app/scripts/self-test.sh
#   ADMIN_EMAIL=owner@x.com ADMIN_PASSWORD=... /app/scripts/self-test.sh
#
# Defaults to the canonical owner credentials from test_credentials.md
# so the script works out-of-the-box on the preview environment.

set -euo pipefail

API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2-)
EMAIL="${ADMIN_EMAIL:-admin@risedual.ai}"
PASS="${ADMIN_PASSWORD:-RiseDual2026!}"

if [[ -z "${API_URL}" ]]; then
    echo "ERROR: REACT_APP_BACKEND_URL not set in /app/frontend/.env" >&2
    exit 2
fi

echo "▶ RISEDUAL AI Self-Test"
echo "  API: ${API_URL}"
echo "  User: ${EMAIL}"
echo

# 1. Login to get a session token
LOGIN_RES=$(curl -s -X POST "${API_URL}/api/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASS}\"}")

TOKEN=$(echo "${LOGIN_RES}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('token') or d.get('access_token') or '')
except Exception:
    pass
")

if [[ -z "${TOKEN}" ]]; then
    echo "❌ Login failed. Response was:" >&2
    echo "${LOGIN_RES}" >&2
    exit 2
fi

# 2. Hit the self-test endpoint
REPORT=$(curl -s -X POST "${API_URL}/api/admin/self-test" \
    -H "Authorization: Bearer ${TOKEN}")

# 3. Pretty-print and exit with the overall status
python3 - <<PYEOF
import json, sys

try:
    r = json.loads('''${REPORT}''')
except Exception as e:
    print(f"❌ Could not parse self-test response: {e}", file=sys.stderr)
    print('''${REPORT}''', file=sys.stderr)
    sys.exit(2)

ICON = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ "}
bar = "─" * 64

print(bar)
print(f" RISEDUAL AI Self-Test  —  {r['overall']}")
print(f" {r['passed']}/{r['total']} passed · {r['failed']} failed · {r['warned']} warn")
print(f" timestamp: {r['timestamp']}")
print(bar)

for c in r.get("checks", []):
    icon = ICON.get(c["status"], "?")
    line = f" {icon} {c['name']:<24} {c['status']}"
    if c.get("info"):
        line += f"  ·  {c['info']}"
    if c.get("error"):
        line += f"  ·  {c['error']}"
    print(line)

print(bar)

sys.exit(0 if r["overall"] == "PASS" else 1)
PYEOF
