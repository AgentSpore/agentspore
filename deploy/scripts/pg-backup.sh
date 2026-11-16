#!/usr/bin/env bash
# pg-backup.sh - daily pg_dump (custom format) of the database inside a postgres container.
#
# Credentials never leave the container: pg_dump runs through `docker exec` and reads
# POSTGRES_USER / POSTGRES_DB from the container's own environment.
#
# Output : $BACKUP_DIR/agentspore-<UTC timestamp>.dump          (daily)
#          $BACKUP_DIR/weekly/agentspore-<UTC timestamp>.dump   (copy of Sunday dumps)
# A dump is written to a hidden temp name and renamed only after `pg_restore -l`
# reads the whole archive and lists more than MIN_TOC entries. Final stdout line is one JSON summary:
#   {"backup":"ok","size_bytes":N,"toc":M}   or   {"backup":"fail","stage":"..."}
# pg_dump runs under an in-container `timeout 6600` (below the unit's 2h TimeoutStartSec).
# Exit code is non-zero on failure. No row data and no env values are printed.
#
# Env (all optional):
#   BACKUP_DIR     default /var/backups/agentspore-pg
#   DB_CONTAINER   default agentspore-db
#   KEEP_DAILY     default 7
#   KEEP_WEEKLY    default 4 (Sunday dumps, UTC)
#   MIN_TOC        default 100 (dump rejected unless TOC has MORE entries)
#   SPACE_FACTOR   default 5 (abort if free space < SPACE_FACTOR x newest dump)
#   BACKUP_NOW     override the UTC timestamp (YYYYMMDDTHHMMSSZ), for tests

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/agentspore-pg}"
DB_CONTAINER="${DB_CONTAINER:-agentspore-db}"
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
MIN_TOC="${MIN_TOC:-100}"
SPACE_FACTOR="${SPACE_FACTOR:-5}"
NAME_RE='^agentspore-[0-9]{8}T[0-9]{6}Z\.dump$'

TS="${BACKUP_NOW:-$(date -u +%Y%m%dT%H%M%SZ)}"
FINAL="$BACKUP_DIR/agentspore-$TS.dump"
TMP="$BACKUP_DIR/.agentspore-$TS.dump.part"

trap 'rm -f -- "$TMP"' EXIT

fail() {
    printf '{"backup":"fail","stage":"%s"}\n' "$1"
    exit 1
}

log() { echo "pg-backup: $*" >&2; }

# Regular files in dir $1 whose name matches NAME_RE, one per line (unsorted).
list_dumps() {
    find "$1" -maxdepth 1 -type f -printf '%f\n' | { grep -E "$NAME_RE" || true; }
}

check_space() {
    local last size free
    last="$(list_dumps "$BACKUP_DIR" | sort | tail -n 1)"
    [ -n "$last" ] || return 0
    size="$(stat -c %s -- "$BACKUP_DIR/$last")"
    free="$(df --output=avail -B1 -- "$BACKUP_DIR" | tail -n 1 | tr -d ' ')"
    [ "$free" -ge $((size * SPACE_FACTOR)) ]
}

# Keep the $2 newest names (they sort chronologically) matching NAME_RE in dir $1.
rotate() {
    local dir=$1 keep=$2 n=0 deleted=0 f
    [ -d "$dir" ] || return 0
    while IFS= read -r f; do
        n=$((n + 1))
        if [ "$n" -gt "$keep" ]; then
            rm -f -- "$dir/$f"
            deleted=$((deleted + 1))
        fi
    done < <(list_dumps "$dir" | sort -r)
    log "rotated $deleted file(s) in $dir (keep $keep)"
}

# Full-archive read (catches truncated data sections), then the TOC entry count.
dump_toc() {
    docker exec -i "$DB_CONTAINER" pg_restore -f /dev/null < "$TMP" || return 1
    docker exec -i "$DB_CONTAINER" pg_restore -l < "$TMP" | awk '!/^;/ && NF {n++} END {print n + 0}'
}

mkdir -p -- "$BACKUP_DIR" || fail mkdir
exec 9> "$BACKUP_DIR/.pg-backup.lock" || fail lock
flock -n 9 || fail lock

# Leftovers of a killed run; the age guard spares a dump still being written.
find "$BACKUP_DIR" -maxdepth 1 -type f -name '.agentspore-*.dump.part' -mmin +180 -delete

check_space || fail diskspace

docker exec "$DB_CONTAINER" sh -c 'exec timeout 6600 pg_dump --lock-wait-timeout=60s -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$TMP" || fail dump
[ -s "$TMP" ] || fail dump

TOC="$(dump_toc)" || fail verify
case "$TOC" in '' | *[!0-9]*) fail verify ;; esac
[ "$TOC" -gt "$MIN_TOC" ] || fail verify

mv -- "$TMP" "$FINAL" || fail rename
SIZE="$(stat -c %s -- "$FINAL")" || fail rename

if [ "$(date -u -d "${TS:0:8}" +%u)" = 7 ]; then
    mkdir -p -- "$BACKUP_DIR/weekly" || fail weekly
    ln -f -- "$FINAL" "$BACKUP_DIR/weekly/agentspore-$TS.dump" 2> /dev/null \
        || cp -p -- "$FINAL" "$BACKUP_DIR/weekly/agentspore-$TS.dump" || fail weekly
fi

rotate "$BACKUP_DIR" "$KEEP_DAILY"
rotate "$BACKUP_DIR/weekly" "$KEEP_WEEKLY"

printf '{"backup":"ok","size_bytes":%s,"toc":%s}\n' "$SIZE" "$TOC"
