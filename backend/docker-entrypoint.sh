#!/bin/sh
# Validates UVICORN_WORKERS (default 4), then execs the CMD so it stays PID 1.
# uvicorn reads UVICORN_WORKERS itself; this only rejects values it would
# silently coerce (0) or report unclearly. Each worker is ~175 MiB RSS.
set -eu

workers="${UVICORN_WORKERS:-4}"
case "$workers" in
    '' | *[!0-9]* | 0*)
        echo "docker-entrypoint: UVICORN_WORKERS must be a positive integer without leading zeros, got '$workers'" >&2
        exit 64
        ;;
esac
export UVICORN_WORKERS="$workers"

exec "$@"
