#!/usr/bin/env bash
# RISEDUAL AI — Preview → Production Mongo migration
# ──────────────────────────────────────────────────
# Mitigates audit warning W1 from the deployment-readiness scan:
# the preview pod's MongoDB is NOT carried over to the deploy
# environment by default. Run this once between clicking
# "Deploy" and going live so production starts with the same
# users, predictions, proof-chain history, alert rules, and
# adaptation state that the preview has been accumulating.
#
# Architecture
# ────────────
# 1. mongodump from the preview source (default: localhost
#    in-pod mongodb) → /tmp/rd-mongo-dump/<db>/.bson + .json
#    metadata files. BSON preserves type fidelity (Decimal128,
#    ObjectId, tz-aware datetime) — no JSON-coercion lossiness.
# 2. mongorestore into the destination URI with --drop, so
#    re-running is idempotent.
# 3. Smoke-validation: doc counts before vs after match for a
#    handful of mission-critical collections (users,
#    predictions, proof_chain_blocks, integrity_mitigations,
#    crypto_paper_trades).
#
# Collections that should NOT migrate
# ────────────────────────────────────
# * Anything starting with ``test_`` / ``_test``
# * ``brute_force_events`` (preview-only IP lockouts; real
#   prod offenders will be different IPs)
# * ``mongo_chroma_drift_history`` (pod-local sparkline data)
#
# An exclusion list lives in EXCLUDED_COLLECTIONS below — extend
# it as you discover more preview-only state.
#
# Usage
# ─────
# Dry run (dump only, no restore):
#   ./migrate_preview_to_prod.sh dump
# Full migration:
#   SOURCE_MONGO_URL="$PREVIEW_MONGO_URL" \
#   DEST_MONGO_URL="$PROD_MONGO_URL" \
#   SOURCE_DB=test_database \
#   DEST_DB=test_database \
#   ./migrate_preview_to_prod.sh migrate
# Smoke-only (compare counts on the dest after restore):
#   ./migrate_preview_to_prod.sh smoke

set -euo pipefail

SOURCE_MONGO_URL="${SOURCE_MONGO_URL:-mongodb://localhost:27017}"
DEST_MONGO_URL="${DEST_MONGO_URL:-}"
SOURCE_DB="${SOURCE_DB:-test_database}"
DEST_DB="${DEST_DB:-${SOURCE_DB}}"
DUMP_DIR="${DUMP_DIR:-/tmp/rd-mongo-dump}"

EXCLUDED_COLLECTIONS=(
  "brute_force_events"
  "mongo_chroma_drift_history"
)

# Collections we always want to validate post-restore.
SMOKE_COLLECTIONS=(
  "users"
  "predictions"
  "proof_chain_blocks"
  "integrity_mitigations"
  "data_integrity_alert_rules"
  "crypto_paper_trades"
  "trade_orders"
)

cmd="${1:-help}"

log()  { printf '\033[0;36m[migrate]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[migrate]\033[0m %s\n' "$*"; }
fail() { printf '\033[0;31m[migrate]\033[0m %s\n' "$*" >&2; exit 1; }

require_dest() {
  if [[ -z "${DEST_MONGO_URL}" ]]; then
    fail "DEST_MONGO_URL is required for this command. Set it to the production Mongo URI."
  fi
}

build_excluded_args() {
  local args=()
  for c in "${EXCLUDED_COLLECTIONS[@]}"; do
    args+=("--excludeCollection=${c}")
  done
  printf '%s\n' "${args[@]}"
}

count_docs() {
  # $1 = mongo_url, $2 = db, $3 = collection
  mongosh --quiet --eval \
    "db.getSiblingDB('$2').getCollection('$3').estimatedDocumentCount()" \
    "$1" 2>/dev/null | tail -1
}

do_dump() {
  log "Dumping ${SOURCE_DB} from ${SOURCE_MONGO_URL%@*}@... → ${DUMP_DIR}"
  rm -rf "${DUMP_DIR}"
  mkdir -p "${DUMP_DIR}"
  local exclude_args=()
  while IFS= read -r line; do
    [[ -n "${line}" ]] && exclude_args+=("${line}")
  done < <(build_excluded_args)

  mongodump \
    --uri="${SOURCE_MONGO_URL}" \
    --db="${SOURCE_DB}" \
    --out="${DUMP_DIR}" \
    "${exclude_args[@]}"
  log "Dump complete. Size: $(du -sh "${DUMP_DIR}" | cut -f1)"
  log "Collections dumped: $(find "${DUMP_DIR}/${SOURCE_DB}" -name '*.bson' 2>/dev/null | wc -l)"
}

do_restore() {
  require_dest
  if [[ ! -d "${DUMP_DIR}/${SOURCE_DB}" ]]; then
    fail "Dump dir ${DUMP_DIR}/${SOURCE_DB} not found. Run 'dump' first."
  fi
  warn "About to RESTORE into ${DEST_MONGO_URL%@*}@... db=${DEST_DB} (with --drop)"
  warn "This will overwrite existing collections in the destination."
  read -p "Proceed? [yes/N] " -r confirm
  if [[ "${confirm}" != "yes" ]]; then
    log "Aborted by user."
    exit 0
  fi
  mongorestore \
    --uri="${DEST_MONGO_URL}" \
    --nsFrom="${SOURCE_DB}.*" \
    --nsTo="${DEST_DB}.*" \
    --drop \
    --preserveUUID \
    --stopOnError=false \
    "${DUMP_DIR}"
  log "Restore complete."
}

do_smoke() {
  require_dest
  log "Smoke-validating doc counts: ${SOURCE_DB} vs ${DEST_DB}"
  local fail_count=0
  printf '\n  %-32s %10s %10s %s\n' COLLECTION SOURCE DEST STATUS
  printf '  %-32s %10s %10s %s\n' '--------------------------------' '----------' '----------' '------'
  for coll in "${SMOKE_COLLECTIONS[@]}"; do
    local src dst
    src="$(count_docs "${SOURCE_MONGO_URL}" "${SOURCE_DB}" "${coll}" 2>/dev/null || echo '?')"
    dst="$(count_docs "${DEST_MONGO_URL}" "${DEST_DB}" "${coll}" 2>/dev/null || echo '?')"
    local status='ok'
    if [[ "${src}" != "${dst}" ]]; then
      status='MISMATCH'
      fail_count=$((fail_count+1))
    fi
    printf '  %-32s %10s %10s %s\n' "${coll}" "${src}" "${dst}" "${status}"
  done
  echo
  if [[ "${fail_count}" -gt 0 ]]; then
    warn "${fail_count} collection(s) mismatched. Investigate before going live."
    exit 2
  fi
  log "Smoke validation passed."
}

case "${cmd}" in
  dump)
    do_dump
    ;;
  restore)
    do_restore
    ;;
  migrate)
    do_dump
    do_restore
    do_smoke
    ;;
  smoke)
    do_smoke
    ;;
  help|*)
    cat <<EOF
RISEDUAL preview → prod Mongo migration

Commands:
  dump      Dump the source DB to ${DUMP_DIR}.
  restore   Restore an existing dump into DEST_MONGO_URL.
  migrate   Dump + restore + smoke (the full workflow).
  smoke     Compare doc counts between source and dest.
  help      This message.

Environment:
  SOURCE_MONGO_URL  default: mongodb://localhost:27017
  SOURCE_DB         default: test_database
  DEST_MONGO_URL    REQUIRED for restore/migrate/smoke
  DEST_DB           default: same as SOURCE_DB
  DUMP_DIR          default: /tmp/rd-mongo-dump

Excluded from migration (preview-only state):
$(printf '  - %s\n' "${EXCLUDED_COLLECTIONS[@]}")

Smoke-validated collections:
$(printf '  - %s\n' "${SMOKE_COLLECTIONS[@]}")
EOF
    ;;
esac
