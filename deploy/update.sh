#!/bin/bash
set -euo pipefail

# AgentsSpore — Pull latest code and redeploy
# Called by webhook listener or manually

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
LOG="/var/log/agentspore-deploy.log"

echo "$(date '+%Y-%m-%d %H:%M:%S') === Starting update ===" >> "$LOG"

# 1. Pull latest
cd "$REPO_DIR"
git fetch origin main >> "$LOG" 2>&1
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Already up to date ($LOCAL)" >> "$LOG"
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Updating $LOCAL -> $REMOTE" >> "$LOG"
git reset --hard origin/main >> "$LOG" 2>&1

# 2. Rebuild and restart
cd "$DEPLOY_DIR"
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build --remove-orphans >> "$LOG" 2>&1

# 3. Verify
sleep 10
if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Deploy OK ($(git -C "$REPO_DIR" rev-parse --short HEAD))" >> "$LOG"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') WARNING: Health check failed after deploy" >> "$LOG"
fi
