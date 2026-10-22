# Service access logs

Owner: AgentSpore maintainers. Last reviewed: 2026-10-22.

Caddy 2.11.2 writes one JSON record per HTTP request for the platform,
its www/API redirects, wildcard services, and the three restored applications.
Both .com and .ru hosts use the same `service_access_log` snippet.
Restored applications use fixed service names; platform entries use fixed
configured host names. Wildcard routes use their configured wildcard label.

The six fields are `ts` (Unix seconds), `service`, `method`, `status`,
`duration` (seconds), and `size` (response bytes). Methods outside the
configured HTTP method set become `OTHER`. Successes and failures are counted.
Requests are not people, and these records cannot identify unique visitors.

The encoder deletes the complete request and response-header objects, user
identity, request size, and error details. JSON message, logger, level, caller,
and stacktrace keys are disabled. No request paths, query strings, bodies,
headers, client addresses, or TLS fingerprints belong in this access file.
This contract applies to access records, not all Caddy operational diagnostics.
The native filter is a deletion filter, not a general whitelist: upgrading
Caddy or adding log fields requires rerunning the exact-schema privacy tests.

`/var/log/caddy/services.jsonl` uses mode `0600` in the named `caddy_logs`
volume. The volume survives container recreation. Rotation uses 25 MiB,
four archives, and `168h` retention for rotated files. Archive cleanup happens
on rotation; the active file does not have a strict seven-day expiry.
Do not expose this volume through HTTP or mount it into application containers.

## Release checks

Run the targeted native fixture from the repository root:

```sh
uv run --offline --no-project --python backend/.venv/bin/python3.12 pytest -q deploy/tests/test_service_access_logs.py --basetemp "$PWD/.pytest_cache/service-access-logs"
```

The fixture requires the existing local preflight image and Docker.
Its temporary directory must be shared with the Docker daemon on macOS. It adapts
all public production routes with a synthetic DNS token, then runs the same
logging snippet against synthetic successes, rejected requests, and unavailable
upstreams. It checks exact fields, service routing, one record per request,
file permissions, and absence of synthetic sensitive values.

Before a Caddy-only release, archive the old container-layer
`/var/log/caddy/access.log` opaquely to a root-only `0600` archive. Never print
or inspect its contents. Keep the archive and the old image for rollback.
Adapt the candidate with the actual pinned binary without dumping config
values. Retain the current compose prefix and add only the log-volume mount;
do not rebuild applications or change proxy trust settings.

After the reviewed Caddy-only recreation, verify the named-volume mount,
mode, and one safe control request per host using aggregate counts only.
Recreate Caddy once more under the release gate and confirm the same volume
and preexisting safe control count remain. A config reload alone does not
apply changed file-writer options for an already-open filename.
Rollback uses the previous reviewed Caddy configuration and compose prefix;
preserve the new volume and old archive. Never prune volumes to roll back.
