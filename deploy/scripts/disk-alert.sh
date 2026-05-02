#!/usr/bin/env bash
# disk-alert.sh — check disk usage and DM AdminAgentSpore if above threshold.
# Deploy to both servers. Runs via cron.
#
# Usage: disk-alert.sh [THRESHOLD_PERCENT]
# Default threshold: 75
#
# Required env:
#   AGENTSPORE_API_KEY  — AgentSporeDevOps key (set in cron env, never hardcoded)
#   AGENTSPORE_API_URL  — e.g. https://agentspore.com (default)
#   ADMIN_AGENT_HANDLE  — default: adminagentspore
#
# Cron example (prod server):
#   */30 * * * * AGENTSPORE_API_KEY=<key> /opt/scripts/disk-alert.sh >> /var/log/disk-alert.log 2>&1

set -euo pipefail

THRESHOLD="${1:-75}"
API_URL="${AGENTSPORE_API_URL:-https://agentspore.com}"
ADMIN_HANDLE="${ADMIN_AGENT_HANDLE:-adminagentspore}"
HOSTNAME_LABEL="$(hostname -s)"

# Collect all mount points over threshold (skip tmpfs/boot/efi)
ALERTS=""
while IFS= read -r line; do
    USE_PCT=$(echo "$line" | awk '{print $5}' | tr -d '%')
    MOUNT=$(echo "$line" | awk '{print $6}')
    FS=$(echo "$line" | awk '{print $1}')

    # Skip tmpfs, devtmpfs, boot partitions
    case "$FS" in tmpfs|devtmpfs|udev) continue ;; esac
    case "$MOUNT" in /boot*|/run*) continue ;; esac

    if [ "$USE_PCT" -ge "$THRESHOLD" ]; then
        USED=$(echo "$line" | awk '{print $3}')
        SIZE=$(echo "$line" | awk '{print $2}')
        ALERTS="${ALERTS}\n  ${MOUNT}: ${USE_PCT}% used (${USED}/${SIZE})"
    fi
done < <(df -h | tail -n +2)

if [ -z "$ALERTS" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] OK — all mounts below ${THRESHOLD}%"
    exit 0
fi

MESSAGE="DISK ALERT [${HOSTNAME_LABEL}] — disk usage >= ${THRESHOLD}%:$(printf '%b' "$ALERTS")"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ALERT: $MESSAGE"

# Send DM to AdminAgentSpore via platform API
# Resolves admin agent handle → sends chat message
HTTP_STATUS=$(curl -s -o /tmp/disk-alert-resp.json -w "%{http_code}" \
    -X POST "${API_URL}/api/v1/chat/message" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${AGENTSPORE_API_KEY}" \
    --data-raw "{\"handle\": \"${ADMIN_HANDLE}\", \"message\": $(printf '%s' "$MESSAGE" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}" \
    --max-time 10 || true)

if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Alert sent OK (HTTP $HTTP_STATUS)"
else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Alert send FAILED (HTTP $HTTP_STATUS). Response: $(cat /tmp/disk-alert-resp.json 2>/dev/null || echo 'empty')"
    exit 1
fi
