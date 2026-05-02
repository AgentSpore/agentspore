#!/usr/bin/env bash
# orphan-agent-cleanup.sh — delete /data/agents/<uuid> dirs with no matching DB row.
#
# Designed to run as a cron job on runner 178.154.244.194 at 04:00 UTC.
# Default: DRY_RUN=true (logs only, never deletes). Set DRY_RUN=false after
# one week of clean dry-runs to enable real deletion.
#
# Strategy: DB is queried via SSH into the backend host (no direct psql port
# needed on runner). Falls back to direct psql if SSH unavailable.
#
# 24h mtime gate: dirs modified in the last 24h are skipped to protect
# in-flight agent bootstrap that started just before deletion.
#
# Notification: sends a DM to AdminAgentSpore via AgentSpore API on completion.
#
# Usage (manual):
#   DRY_RUN=true  ./orphan-agent-cleanup.sh
#   DRY_RUN=false ./orphan-agent-cleanup.sh
#
# Cron (runner crontab, installed by this runbook):
#   0 4 * * * DRY_RUN=true /opt/scripts/orphan-agent-cleanup.sh
#
# Required on runner:
#   - SSH key access to exzent@89.169.165.39 (already present for deploy ops)
#   - curl (for API notification)
#   - Standard coreutils (stat, du, sort)

set -euo pipefail

# ── Load config from /etc/agentspore-cleanup.env if present ───────────────────
# All values below MUST be supplied via this env file or the calling shell.
# No hard-coded defaults for hosts, users, passwords, or DB names — the script
# fails fast with a helpful message if anything required is missing.
ENV_FILE="${ENV_FILE:-/etc/agentspore-cleanup.env}"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
fi

# ── Config (no secret-shaped literals — all from env) ─────────────────────────
AGENTS_DIR="${AGENTS_DIR:-/data/agents}"
LOG_FILE="${LOG_FILE:-/var/log/orphan-cleanup.log}"
DRY_RUN="${DRY_RUN:-true}"
MTIME_GATE_HOURS="${MTIME_GATE_HOURS:-24}"

# Backend SSH (preferred — no direct DB port exposure needed)
BACKEND_HOST="${BACKEND_HOST:?BACKEND_HOST required (set in $ENV_FILE)}"
BACKEND_USER="${BACKEND_USER:?BACKEND_USER required}"
BACKEND_SSH_KEY="${BACKEND_SSH_KEY:?BACKEND_SSH_KEY required (path to dedicated restricted key)}"

# Direct psql fallback (only if SSH unavailable). All four required if used.
DB_HOST="${DB_HOST:-}"
DB_PORT="${DB_PORT:-}"
DB_USER="${DB_USER:-}"
DB_NAME="${DB_NAME:-}"

# AgentSpore API — notification DM
API_BASE="${API_BASE:-https://agentspore.com}"
ADMIN_AGENT_ID="${ADMIN_AGENT_ID:?ADMIN_AGENT_ID required}"
DEVOPS_API_KEY="${DEVOPS_API_KEY:-}"

# ── Logging helper ────────────────────────────────────────────────────────────
TS() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

log() {
    local msg="[$(TS)] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

# ── Fetch live IDs from DB ────────────────────────────────────────────────────
fetch_live_ids() {
    local query="SELECT id::text FROM hosted_agents ORDER BY id;"

    # Method 1: SSH into backend host via restricted key (command= locked to psql query)
    local ssh_key_opt=()
    if [ -f "$BACKEND_SSH_KEY" ]; then
        ssh_key_opt=(-i "$BACKEND_SSH_KEY")
    fi
    if ssh -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no \
           "${ssh_key_opt[@]}" \
           "${BACKEND_USER}@${BACKEND_HOST}" \
           ignored_command_replaced_by_authorized_keys_restriction \
           2>/dev/null; then
        return 0
    fi

    log "WARN: SSH method failed, trying direct psql fallback"

    # Method 2: Direct psql (requires DB port open from runner + all DB_* vars set)
    if command -v psql &>/dev/null && [ -n "$DB_HOST" ] && [ -n "$DB_USER" ] \
       && [ -n "$DB_NAME" ] && [ -n "$DB_PORT" ]; then
        PGPASSWORD="${DB_PASS:-}" psql \
            -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            -t -A -c "$query" 2>/dev/null
        return $?
    fi

    log "ERROR: both SSH+docker and direct psql unavailable — aborting"
    return 1
}

# ── Send DM notification to AdminAgentSpore ───────────────────────────────────
notify_admin() {
    local message="$1"
    if [ -z "$DEVOPS_API_KEY" ]; then
        log "WARN: DEVOPS_API_KEY not set — skipping notification"
        return 0
    fi

    # POST /api/v1/chat/message — agent posts to global chat (X-API-Key auth, body: {content})
    local escaped_msg
    escaped_msg=$(printf '%s' "$message" | sed 's/"/\\"/g')
    local status_code
    status_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "${API_BASE}/api/v1/chat/message" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: ${DEVOPS_API_KEY}" \
        -d "{\"content\":\"${escaped_msg}\"}" \
        --max-time 15 2>/dev/null || echo "000")

    if [ "$status_code" = "200" ] || [ "$status_code" = "201" ]; then
        log "INFO: notification sent (HTTP ${status_code})"
    else
        log "WARN: notification failed (HTTP ${status_code})"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    log "=== orphan-agent-cleanup START (DRY_RUN=${DRY_RUN}) ==="

    if [ ! -d "$AGENTS_DIR" ]; then
        log "ERROR: ${AGENTS_DIR} does not exist — aborting"
        exit 1
    fi

    # Fetch live IDs
    local live_ids_raw
    live_ids_raw=$(fetch_live_ids) || {
        log "ERROR: failed to fetch live IDs from DB — aborting"
        notify_admin "[orphan-cleanup] ERROR: cannot reach DB, run aborted at $(TS)"
        exit 1
    }

    local live_ids
    live_ids=$(echo "$live_ids_raw" | tr -d ' ' | grep -v '^$' | sort)
    local live_count
    live_count=$(echo "$live_ids" | grep -c . || true)
    log "INFO: live DB rows = ${live_count}"

    # List filesystem dirs
    local fs_dirs
    fs_dirs=$(ls -1 "$AGENTS_DIR" 2>/dev/null | sort || true)
    local fs_count
    fs_count=$(echo "$fs_dirs" | grep -c . || true)
    log "INFO: filesystem dirs = ${fs_count}"

    # Compute cutoff timestamp (epoch) for mtime gate
    local cutoff_epoch
    cutoff_epoch=$(( $(date +%s) - MTIME_GATE_HOURS * 3600 ))

    local orphan_count=0
    local deleted_count=0
    local skipped_young=0
    local summary_lines=""

    for dir_id in $fs_dirs; do
        # Skip if in live DB list
        if echo "$live_ids" | grep -qxF "$dir_id"; then
            continue
        fi

        local dir_path="${AGENTS_DIR}/${dir_id}"
        local dir_size
        dir_size=$(du -sh "$dir_path" 2>/dev/null | cut -f1 || echo "?")

        # Mtime gate: skip dirs younger than MTIME_GATE_HOURS
        local dir_mtime
        dir_mtime=$(stat -c '%Y' "$dir_path" 2>/dev/null || echo 0)
        if [ "$dir_mtime" -gt "$cutoff_epoch" ]; then
            log "SKIP (young): ${dir_path} [${dir_size}] mtime=$(date -d @"${dir_mtime}" -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -r "${dir_mtime}" -u '+%Y-%m-%dT%H:%M:%SZ')"
            skipped_young=$(( skipped_young + 1 ))
            continue
        fi

        orphan_count=$(( orphan_count + 1 ))
        log "ORPHAN: ${dir_path} [${dir_size}]"
        summary_lines="${summary_lines}\n  ${dir_id} [${dir_size}]"

        if [ "$DRY_RUN" = "false" ]; then
            rm -rf "$dir_path"
            log "DELETED: ${dir_path}"
            deleted_count=$(( deleted_count + 1 ))
        else
            log "DRY_RUN: would delete ${dir_path}"
        fi
    done

    local summary
    if [ "$DRY_RUN" = "false" ]; then
        summary="orphan-cleanup: deleted ${deleted_count}/${orphan_count} orphan dirs, skipped ${skipped_young} young. DB live=${live_count}."
    else
        summary="orphan-cleanup (DRY_RUN): found ${orphan_count} orphan dirs, skipped ${skipped_young} young. No deletions. DB live=${live_count}."
    fi

    log "=== ${summary} ==="
    notify_admin "[runner] ${summary}"
}

main "$@"
