# A4 builder runbook

Isolated build stack for untrusted agent repositories (A4 phase P3a). Definition:
`deploy/a4-builder.compose.yml`, configs in `deploy/a4-builder/`. The stack is a separate
compose project; the existing platform stack is not changed by it.

## Layout

| Service | Role | Networks |
|---|---|---|
| `buildkitd` | rootless BuildKit under gVisor (`runtime: runsc`), 1 CPU, 2 GiB, `pids_limit` 512, no host socket, state in an anonymous volume | `a4-builder` (internal) |
| `mirror-gate` | nginx allowlist in front of the mirror: GET/HEAD of manifests and sha256 blobs of `library/{python,node,golang,rust,alpine,debian,busybox}` only | `a4-builder`, `a4-mirror-back` |
| `mirror` | `registry:2` pull-through cache of Docker Hub | `a4-mirror-back`, `a4-upstream` |
| `egress-proxy` | squid: HTTPS CONNECT to `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `proxy.golang.org`, `crates.io` (plus `index.`/`static.crates.io` for cargo); private, link-local and metadata IPs denied | `a4-builder`, `a4-upstream` |

`a4-builder` and `a4-mirror-back` are `internal`: `buildkitd` has no route to the internet or to
platform networks. `docker.io` in `buildkitd.toml` is mirrored to `mirror-gate:5000`, so base images
come only from the allowlist; the Dockerfile scanner (P2) is a second layer. `RUN` steps reach
package registries only through `http://egress-proxy:3128`; the builder (P3b) passes
`HTTP_PROXY`/`HTTPS_PROXY` as build args. The future worker joins the external network `a4-builder`
to reach `buildkitd:1234`.

To allow another base repository, edit `deploy/a4-builder/mirror-gate.conf` and the `MIRROR_BASES`
set in `backend/tests/test_a4_builder_compose.py` together.

## Check gVisor on the host

```bash
docker info --format '{{json .Runtimes}}'   # must list "runsc"
runsc --version                              # release-20261012.0 on prod
ls -l /dev/kvm                               # present on prod: runsc platform kvm
docker run --rm --runtime=runsc busybox dmesg | head -3   # gVisor banner, not the host kernel log
```

Prod already has `runsc` registered in Docker 29.9.1; nothing to install. If `runsc` is missing
(for example on a laptop), `docker compose up` for `buildkitd` fails with `unknown or invalid
runtime name: runsc`; do not substitute `runc`.

## Start and stop

```bash
cd /path/to/agentsspore
docker compose -p a4-builder -f deploy/a4-builder.compose.yml up -d
docker compose -p a4-builder -f deploy/a4-builder.compose.yml ps
docker compose -p a4-builder -f deploy/a4-builder.compose.yml down -v   # stop, drop state
```

`buildkitd` has `restart: "no"` and an anonymous state volume: `down -v` (or `rm -fv buildkitd`
then `up -d buildkitd`) gives a fresh daemon with an empty cache. Recycle it after every build.
The mirror cache is a 2 GiB tmpfs and is also dropped on `down`.

## Rollback

`docker compose -p a4-builder -f deploy/a4-builder.compose.yml down -v`. Nothing on the existing
stack changes; the networks `a4-builder`, `a4-mirror-back` and `a4-upstream` go with it. To
remove the files, revert the PR.

## Smoke checks

Run a probe on the builder network (image already pinned in the compose file):

```bash
IMG=$(docker compose -p a4-builder -f deploy/a4-builder.compose.yml config --images | grep nginx)
probe() { docker run --rm --network a4-builder --entrypoint sh "$IMG" -c "$1"; }
probe 'wget -S -q -O /dev/null --header "Accept: application/vnd.oci.image.index.v1+json" \
  http://mirror-gate:5000/v2/library/python/manifests/3.12-slim 2>&1 | grep HTTP'   # 200
probe 'wget -S -q -O /dev/null http://mirror-gate:5000/v2/library/ubuntu/manifests/latest 2>&1 | grep HTTP'  # 403
probe 'printf "CONNECT pypi.org:443 HTTP/1.1\r\n\r\n" | nc egress-proxy 3128 | head -1'    # 200 Connection established
probe 'printf "CONNECT 169.254.169.254:443 HTTP/1.1\r\n\r\n" | nc egress-proxy 3128 | head -1'  # 403
probe 'printf "CONNECT 10.0.0.1:443 HTTP/1.1\r\n\r\n" | nc egress-proxy 3128 | head -1'         # 403
probe 'wget -q -T 5 -O /dev/null http://1.1.1.1'                    # fails: Network unreachable
```

Inside `buildkitd`: `docker exec <buildkitd> buildctl --addr tcp://127.0.0.1:1234 debug workers`
lists one worker. Hardening is also asserted statically by
`python -m pytest backend/tests/test_a4_builder_compose.py` (no Docker needed).

## Known limits

- The 4 GiB build-disk and 10 minute limits are enforced by the builder (P3b), not by compose.
- `buildkitd` under `runsc` is not exercised by CI; check it on the stand before enabling
  `A4_DEPLOY_ENABLED`.
- Docker Hub anonymous pull limits apply to the mirror; failures show as 5xx from `mirror-gate`.
