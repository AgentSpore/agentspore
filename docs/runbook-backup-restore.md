# Runbook: PostgreSQL backup and restore

Status: 2026-11-16, type: how-to. Checked against `deploy/docker-compose.prod.yml`
(container `agentspore-db`, PostgreSQL 16). Install and manual-run steps need systemd and Docker on the host.

This runbook installs a daily `pg_dump` of the AgentSpore database, keeps 7 daily and 4 weekly
dumps, and shows how to prove a dump restores and how to restore it into production.
Dumps stay on the same host: copying them elsewhere is the operator's job ([Off-host copies](#off-host-copies)).
Failures show up only in the journal until you add an alert.

## What the backup does

| Item | Behaviour |
|---|---|
| When | `agentspore-pg-backup.timer`: 03:00 UTC daily plus up to 30 min random delay, `Persistent=true` |
| Dump | `pg_dump -Fc` inside the DB container through `docker exec`. Credentials stay in the container (`POSTGRES_USER`, `POSTGRES_DB`) |
| Verify | Written to a hidden `.part` file. Renamed to `agentspore-<UTC>.dump` only after `pg_restore` reads the whole archive and lists more than `MIN_TOC` (100) entries |
| Rotation | Keeps 7 daily dumps. Sunday (UTC) dumps are also kept in `weekly/`, 4 of them. Only files named `agentspore-<YYYYMMDD>T<HHMMSS>Z.dump` are ever deleted |
| Disk guard | Aborts when free space is below 5 x the newest dump |
| Journal | One JSON line: `{"backup":"ok","size_bytes":N,"toc":M}` or `{"backup":"fail","stage":"..."}` plus a non-zero exit |
| Does NOT | Copy dumps off the host, send alerts, back up Redis, the runner, or Caddy data |

Failure stages: `mkdir`, `lock`, `diskspace`, `dump`, `verify`, `rename`, `weekly`.
A failed run is retried by systemd 3 times, 10 minutes apart (limit of 3 starts per 2 hours).

Settings are environment variables of the script: `BACKUP_DIR` (default `/var/backups/agentspore-pg`),
`DB_CONTAINER` (`agentspore-db`), `KEEP_DAILY`, `KEEP_WEEKLY`, `MIN_TOC`, `SPACE_FACTOR`.
The unit sets `BACKUP_DIR` and `DB_CONTAINER`; to change another one, add an `Environment=` line.
If you change `BACKUP_DIR`, change `ReadWritePaths=` in the unit to match.

## Install

Run as root on the Docker host, from a checkout of this repository.

```bash
install -D -m 0755 deploy/scripts/pg-backup.sh /usr/local/sbin/agentspore-pg-backup
install -D -m 0644 deploy/systemd/agentspore-pg-backup.service /etc/systemd/system/agentspore-pg-backup.service
install -D -m 0644 deploy/systemd/agentspore-pg-backup.timer /etc/systemd/system/agentspore-pg-backup.timer
install -d -m 0700 /var/backups/agentspore-pg
systemd-analyze verify /etc/systemd/system/agentspore-pg-backup.service /etc/systemd/system/agentspore-pg-backup.timer
systemctl daemon-reload
systemctl enable --now agentspore-pg-backup.timer
systemctl list-timers agentspore-pg-backup.timer --no-pager
```

Expect: `verify` prints nothing; `list-timers` shows the next run at 03:00-03:30 UTC.
Remove any old cron entry or `.sql.gz` job so two backups do not run side by side.

## Run once manually

```bash
systemctl start agentspore-pg-backup.service; echo rc=$?
journalctl -u agentspore-pg-backup.service --since '-5min' --no-pager | tail -5
ls -l /var/backups/agentspore-pg/
```

Expect: `rc=0`; a journal line `{"backup":"ok","size_bytes":...,"toc":...}` with `toc` above 100;
one file `agentspore-<UTC>.dump`, mode `-rw-------`, no `.part` file left.

Running the script by hand works too, as root: `/usr/local/sbin/agentspore-pg-backup`.
Do not copy manual dumps into `BACKUP_DIR` under an `agentspore-<UTC>.dump` name: they count against the 7 and get rotated out.
Keep them one level up, or under another name.

Check health at any time:

```bash
journalctl -u agentspore-pg-backup.service --since '-2days' --no-pager | grep '"backup"'
ls -lh /var/backups/agentspore-pg/ /var/backups/agentspore-pg/weekly/
```

## Restore drill (monthly)

A backup nobody restored is a guess. Restore into a throwaway container, never the live DB.
Use the same PostgreSQL major as production (16).

```bash
D=/var/backups/agentspore-pg/agentspore-<UTC>.dump
docker run -d --name bk-drill --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=drill -e POSTGRES_DB=drill -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine
until docker exec bk-drill pg_isready -U drill -d drill -q; do sleep 1; done; sleep 2
docker exec -i bk-drill pg_restore -U drill -d drill --no-owner --no-acl --exit-on-error < "$D"; echo rc=$?
docker exec bk-drill psql -tA -U drill -d drill -c "select count(*) from information_schema.tables where table_schema='public'"
docker exec agentspore-db sh -c "psql -tA -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"select count(*) from information_schema.tables where table_schema='public'\""
docker rm -f -v bk-drill
```

Expect: `rc=0` and the two table counts equal. No ports are published, so the drill is not reachable from outside.
If the restore fails on a missing extension, use the production image tag instead of `postgres:16-alpine`.

## Restore into production

Destructive: every current row is replaced by the dump. Do the drill on the same file first.

1. Pick the dump and take a safety copy of the current state first:
   `docker exec agentspore-db sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > /var/backups/agentspore-pre-restore-$(date -u +%Y%m%dT%H%M%SZ).dump`
2. Stop everything that writes (add `a4-worker` if the `a4` profile is on):
   `docker compose -f deploy/docker-compose.prod.yml stop backend`
3. Restore in place:
   `docker exec -i agentspore-db sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error' < "$D"; echo rc=$?`
4. Expect `rc=0`. On a non-zero rc stop, keep the backend down, and decide with the owner: the safety copy from step 1 is the way back.
5. Start the backend and check it: `docker compose -f deploy/docker-compose.prod.yml start backend`, then `docker logs --tail 50 agentspore-backend` and the public health URL.

## Rollback and uninstall

```bash
systemctl disable --now agentspore-pg-backup.timer
systemctl stop agentspore-pg-backup.service
rm /etc/systemd/system/agentspore-pg-backup.timer /etc/systemd/system/agentspore-pg-backup.service /usr/local/sbin/agentspore-pg-backup
systemctl daemon-reload
systemctl reset-failed agentspore-pg-backup.service agentspore-pg-backup.timer 2>/dev/null || true
```

Dumps already written stay; delete them by hand if wanted.

## Off-host copies

Backups on the same host are not a full backup: a lost disk or host takes the dumps too.
Copying `BACKUP_DIR` to another machine or object storage (rclone, `aws s3 cp`, rsync over SSH) is the operator's responsibility.
The script ships no uploader and no credentials; keep storage credentials outside the repository.
For failure alerts, add `OnFailure=` to the unit or check that a dump newer than 26 hours exists.
