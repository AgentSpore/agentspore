#!/bin/bash
set -euo pipefail

# AgentSpore — Pull latest code and redeploy
# Called by webhook listener or manually

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
LOG="/var/log/agentspore-deploy.log"

# Working-tree files this deploy tolerates diverging from origin/main. The
# root docker-compose.yml carries host-local secrets for dev use and is not
# read by this deploy (it uses docker-compose.prod.yml), so a local edit to
# it is not a sign of an unmerged prod patch.
RESET_IGNORE="docker-compose.yml"

echo "$(date '+%Y-%m-%d %H:%M:%S') === Starting update ===" >> "$LOG"

if [ ! -f "$DEPLOY_DIR/.env" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ABORT: $DEPLOY_DIR/.env not found" >> "$LOG"
    exit 1
fi

# 1. Pull latest
cd "$REPO_DIR"
git fetch origin main >> "$LOG" 2>&1
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Already up to date ($LOCAL)" >> "$LOG"
    exit 0
fi

# INVARIANT: git reset --hard silently wiped uncommitted prod patches twice
# (2026-07-21, 2026-08-30). Abort instead of discarding a dirty tracked file
# that actually differs from origin/main, unless it is explicitly ignored.
DIRTY_UNRESOLVED=()
while IFS= read -r -d '' f; do
    case " $RESET_IGNORE " in
        *" $f "*) continue ;;
    esac
    if ! git diff --quiet origin/main -- "$f"; then
        DIRTY_UNRESOLVED+=("$f")
    fi
done < <(git diff -z --name-only HEAD)
if [ "${#DIRTY_UNRESOLVED[@]}" -gt 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ABORT: uncommitted changes differ from origin/main: ${DIRTY_UNRESOLVED[*]}" >> "$LOG"
    exit 1
fi

# RESET_IGNORE files still get overwritten by `reset --hard` itself (it does
# not know about the ignore list, only this script does), so back each one up
# and put it back once the reset is done.
RESET_IGNORE_BACKUP="$(mktemp -d)"
for f in $RESET_IGNORE; do
    if [ -f "$REPO_DIR/$f" ]; then
        mkdir -p "$RESET_IGNORE_BACKUP/$(dirname "$f")"
        cp "$REPO_DIR/$f" "$RESET_IGNORE_BACKUP/$f"
    fi
done

echo "$(date '+%Y-%m-%d %H:%M:%S') Updating $LOCAL -> $REMOTE" >> "$LOG"
git reset --hard origin/main >> "$LOG" 2>&1

for f in $RESET_IGNORE; do
    if [ -f "$RESET_IGNORE_BACKUP/$f" ]; then
        cp "$RESET_IGNORE_BACKUP/$f" "$REPO_DIR/$f"
    fi
done
rm -rf "$RESET_IGNORE_BACKUP"

# 2. Rebuild and restart
cd "$DEPLOY_DIR"
docker compose -f docker-compose.prod.yml --env-file .env up -d --build --remove-orphans >> "$LOG" 2>&1

# 3. Verify. The backend is not published on the host, so probe it through
# the container's own loopback instead of curl-ing localhost:8000. Retry
# across the container's warm-up window instead of a single check after a
# fixed sleep.
HEALTHY=false
for _ in $(seq 1 12); do
    if docker exec agentspore-backend curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    sleep 5
done

if [ "$HEALTHY" = true ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Deploy OK ($(git -C "$REPO_DIR" rev-parse --short HEAD))" >> "$LOG"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') WARNING: Health check failed after deploy" >> "$LOG"
    exit 1
fi
