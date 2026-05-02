#!/usr/bin/env bash
# find-orphan-agent-dirs.sh — find /data/agents/<uuid> dirs with no matching DB row.
#
# Run on runner server 178.154.244.194 (or from any host with DB access).
# NEVER auto-deletes. Reports orphans only — human must confirm.
#
# Usage:
#   DB_HOST=89.169.165.39 DB_PORT=5432 DB_PASS=<pass> ./find-orphan-agent-dirs.sh
#   or run directly on runner server after SSH with DB network access.
#
# Required env (runner server):
#   DB_HOST  — main DB host (default: 89.169.165.39)
#   DB_PORT  — (default: 5432)
#   DB_USER  — (default: postgres)
#   DB_NAME  — (default: sporeai)
#   DB_PASS  — postgres password
#
# Alternative: pass PGPASSWORD to psql directly.
#
# Note: on prod server use `docker exec agentspore-db` instead of direct psql.

set -euo pipefail

AGENTS_DIR="${AGENTS_DIR:-/data/agents}"
DB_HOST="${DB_HOST:-89.169.165.39}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-sporeai}"

if [ ! -d "$AGENTS_DIR" ]; then
    echo "ERROR: $AGENTS_DIR does not exist on this host."
    exit 1
fi

echo "=== Orphan agent dir finder ==="
echo "Agents dir : $AGENTS_DIR"
echo "DB         : $DB_HOST:$DB_PORT/$DB_NAME"
echo "Date       : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# Fetch live IDs from DB (no deleted_at column — all rows are considered live)
# If run from runner host, DB access must be reachable (firewall port 5432 open or via SSH tunnel)
DB_IDS_FILE=$(mktemp)
PGPASSWORD="${DB_PASS:-}" psql \
    -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -t -A \
    -c "SELECT id::text FROM hosted_agents;" \
    > "$DB_IDS_FILE" 2>/dev/null || {
    echo "ERROR: Cannot connect to DB. Set DB_PASS env var and ensure network access."
    echo "Fallback: SSH to prod and run:"
    echo "  ssh exzent@89.169.165.39 \"docker exec agentspore-db psql -U postgres -d sporeai -t -A -c 'SELECT id::text FROM hosted_agents;'\""
    rm -f "$DB_IDS_FILE"
    exit 1
}

DB_IDS=$(sort < "$DB_IDS_FILE" | tr -d ' ')
rm -f "$DB_IDS_FILE"

DB_COUNT=$(echo "$DB_IDS" | grep -c . || true)
echo "Live DB rows    : $DB_COUNT"

# List dirs on filesystem
FS_IDS=$(ls -1 "$AGENTS_DIR" 2>/dev/null | sort)
FS_COUNT=$(echo "$FS_IDS" | grep -c . || true)
echo "Filesystem dirs : $FS_COUNT"
echo ""

ORPHANS=()
for dir_id in $FS_IDS; do
    if ! echo "$DB_IDS" | grep -qxF "$dir_id"; then
        ORPHANS+=("$dir_id")
    fi
done

if [ ${#ORPHANS[@]} -eq 0 ]; then
    echo "No orphans found. All filesystem dirs match DB rows."
    exit 0
fi

echo "=== ORPHANS (${#ORPHANS[@]}) — NO auto-delete ==="
echo ""
TOTAL_ORPHAN_SIZE=0
for id in "${ORPHANS[@]}"; do
    SIZE=$(du -sh "${AGENTS_DIR}/${id}" 2>/dev/null | cut -f1 || echo "?")
    echo "  ${AGENTS_DIR}/${id}  [${SIZE}]"
done
echo ""
echo "To delete manually (after confirmation):"
for id in "${ORPHANS[@]}"; do
    echo "  rm -rf ${AGENTS_DIR}/${id}"
done
echo ""
echo "IMPORTANT: Verify each ID is truly absent from DB before deleting."
echo "  docker exec agentspore-db psql -U postgres -d sporeai -c \"SELECT id, status, created_at FROM hosted_agents WHERE id='<uuid>';\""
