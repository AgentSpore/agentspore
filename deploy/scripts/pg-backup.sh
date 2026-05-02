#!/usr/bin/env bash
# pg-backup.sh — daily pg_dump of sporeai DB with 7-day rotation.
#
# Run on prod server 89.169.165.39 via cron.
# Does NOT ship dumps anywhere remote (S3/etc) — add rclone/aws s3 cp call if needed.
#
# Cron (as user exzent, prod server):
#   0 3 * * * /opt/scripts/pg-backup.sh >> /var/log/pg-backup.log 2>&1
#
# Env (loaded from /etc/agentspore-cleanup.env or set in cron):
#   BACKUP_DIR    — default: /var/backups/agentspore-pg
#   RETENTION     — days to keep, default: 7
#   DB_CONTAINER  — required (docker container name running postgres)
#   DB_USER       — required (postgres role)
#   DB_NAME       — required (database to dump)

set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/agentspore-cleanup.env}"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
fi

BACKUP_DIR="${BACKUP_DIR:-/var/backups/agentspore-pg}"
RETENTION="${RETENTION:-7}"
DB_CONTAINER="${DB_CONTAINER:?DB_CONTAINER required (set in $ENV_FILE)}"
DB_USER="${DB_USER:?DB_USER required (set in $ENV_FILE)}"
DB_NAME="${DB_NAME:?DB_NAME required (set in $ENV_FILE)}"
TIMESTAMP="$(date -u +%Y-%m-%d_%H%M%S)"
DUMP_FILE="${BACKUP_DIR}/sporeai_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting pg_dump → $DUMP_FILE"

docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" \
    | gzip > "$DUMP_FILE"

DUMP_SIZE=$(du -sh "$DUMP_FILE" | cut -f1)
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Done. Size: $DUMP_SIZE"

# Rotate: delete dumps older than RETENTION days
DELETED=$(find "$BACKUP_DIR" -name "sporeai_*.sql.gz" -mtime +"$RETENTION" -print -delete 2>/dev/null | wc -l)
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Rotated $DELETED old dump(s) (retention=${RETENTION}d)"

# List current backups
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Current backups:"
ls -lh "$BACKUP_DIR"/sporeai_*.sql.gz 2>/dev/null || echo "  (none)"
