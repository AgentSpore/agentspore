# Runbook: PostgreSQL Backup & Restore

## Overview

Daily `pg_dump` of `sporeai` DB on prod server `89.169.165.39`.
Backups stored locally at `/var/backups/agentspore-pg/`.
Retention: 7 days rolling.

## Setup (one-time, run on prod server)

```bash
# 1. Copy script
sudo mkdir -p /opt/scripts
sudo cp /app/agentsspore/deploy/deploy/scripts/pg-backup.sh /opt/scripts/pg-backup.sh
sudo chmod +x /opt/scripts/pg-backup.sh

# 2. Create backup dir
sudo mkdir -p /var/backups/agentspore-pg
sudo chown exzent:exzent /var/backups/agentspore-pg
chmod 700 /var/backups/agentspore-pg

# 3. Install cron (as exzent)
crontab -e
# Add:
# 0 3 * * * /opt/scripts/pg-backup.sh >> /var/log/pg-backup.log 2>&1
```

## Manual backup (ad-hoc)

```bash
ssh exzent@89.169.165.39
/opt/scripts/pg-backup.sh
# or directly:
docker exec agentspore-db pg_dump -U postgres sporeai | gzip > /var/backups/agentspore-pg/sporeai_manual_$(date +%F).sql.gz
```

## Restore procedure

### Full restore to running container

```bash
# 1. Choose dump
ls -lh /var/backups/agentspore-pg/

# 2. Restore to existing DB (drops and recreates all tables)
#    WARNING: this is destructive — all current data is replaced.
DUMP=/var/backups/agentspore-pg/sporeai_2026-05-02_030000.sql.gz

gunzip -c "$DUMP" | docker exec -i agentspore-db psql -U postgres sporeai

# 3. Verify row counts (sanity check)
docker exec agentspore-db psql -U postgres -d sporeai -c "
  SELECT
    (SELECT COUNT(*) FROM users)          AS users,
    (SELECT COUNT(*) FROM agents)         AS agents,
    (SELECT COUNT(*) FROM hosted_agents)  AS hosted_agents;
"
```

### Restore to a temporary DB (safe verification without touching prod)

```bash
# Create temp DB
docker exec agentspore-db createdb -U postgres sporeai_restore_test

# Restore into it
DUMP=/var/backups/agentspore-pg/sporeai_2026-05-02_030000.sql.gz
gunzip -c "$DUMP" | docker exec -i agentspore-db psql -U postgres sporeai_restore_test

# Verify
docker exec agentspore-db psql -U postgres -d sporeai_restore_test -c "\dt"
docker exec agentspore-db psql -U postgres -d sporeai_restore_test -c "SELECT COUNT(*) FROM users;"

# Drop temp DB when done
docker exec agentspore-db dropdb -U postgres sporeai_restore_test
```

## Test restore (run monthly)

```bash
# From prod server:
LATEST=$(ls -t /var/backups/agentspore-pg/sporeai_*.sql.gz | head -1)
echo "Testing restore of: $LATEST"

# Create test DB
docker exec agentspore-db createdb -U postgres sporeai_restore_test

# Restore
gunzip -c "$LATEST" | docker exec -i agentspore-db psql -U postgres sporeai_restore_test 2>&1 | tail -5

# Drop a non-essential table to simulate data loss
docker exec agentspore-db psql -U postgres -d sporeai_restore_test -c "DROP TABLE IF EXISTS activity_log;"

# Re-restore
gunzip -c "$LATEST" | docker exec -i agentspore-db psql -U postgres sporeai_restore_test 2>&1 | tail -5

# Verify table is back
docker exec agentspore-db psql -U postgres -d sporeai_restore_test -c "\d activity_log"

# Cleanup
docker exec agentspore-db dropdb -U postgres sporeai_restore_test
echo "Restore test PASSED"
```

## Backup health check

```bash
# List backups and sizes
ls -lh /var/backups/agentspore-pg/

# Tail log
tail -50 /var/log/pg-backup.log

# Verify gzip integrity (no corrupted dumps)
for f in /var/backups/agentspore-pg/*.sql.gz; do
    gunzip -t "$f" && echo "OK: $f" || echo "CORRUPT: $f"
done
```

## Off-site backup (recommended before hackathon)

Backups on the same host = no backup. Before hackathon, add S3/Yandex Cloud upload.

```bash
# Example — append to pg-backup.sh after the gzip step:
# rclone copy "$DUMP_FILE" yandex-s3:agentspore-backups/pg/
# or: aws s3 cp "$DUMP_FILE" s3://agentspore-backups/pg/
```

Ask user for S3/YC credentials before enabling — do not store in this file.
