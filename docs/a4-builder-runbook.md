# A4 builder runbook

Isolated build stack for untrusted agent repositories (A4 phase P3a). Definition:
`deploy/a4-builder.compose.yml`, configs in `deploy/a4-builder/`. The stack is a separate
compose project; the existing platform stack is not changed by it.

## Layout

| Service | Role | Networks |
|---|---|---|
| `buildkitd` | rootless BuildKit under gVisor (`runtime: runsc`), 1 CPU, 6 GiB memory (4 GiB tmpfs + 2 GiB working set), `deploy.resources.limits.pids` 512, no host socket, read-only rootfs, state in a 4 GiB `exec` tmpfs (a restart wipes it), mTLS-only listener with an mTLS healthcheck | `a4-builder` (internal) |
| `a4-restart-gate` | nginx in front of the Docker socket (read-only bind, docker group via `A4_DOCKER_GID`): only `POST /v1.NN/containers/a4-builder-buildkitd-1/restart[?t=N]` and `GET .../json` pass, everything else gets 403; no request body is forwarded | `a4-control` only (never `a4-builder`) |
| `mirror-gate` | nginx allowlist in front of the mirror: GET/HEAD of manifests and blobs addressed by `sha256:` digest (tags get 403) for `library/{python,node,golang,rust,alpine,debian,busybox}` only | `a4-builder`, `a4-mirror-back` |
| `mirror` | `registry:3` (distribution v3.0.0) pull-through cache of Docker Hub, `proxy.ttl` 12 h | `a4-mirror-back`, `a4-upstream` |
| `pkg-mirror` | one nginx (digest-pinned, `nginx-unprivileged`) serving read-only HTTP mirrors of PyPI `:8081`, npm `:8082`, Go `:8083`, crates.io `:8084` at `10.213.77.12`; the only package path out of `a4-builder`. Cache in a 2 GiB tmpfs | `a4-builder`, `a4-upstream` |

`a4-builder` and `a4-mirror-back` are `internal`: `buildkitd` has no route to the internet or to
platform networks. `docker.io` in `buildkitd.toml` is mirrored to `mirror-gate:5000`, so base images
come only from the allowlist; the Dockerfile scanner (P2) is a second layer. A `FROM` must carry a
digest (`python:3.12-slim@sha256:...`) because the gate refuses tag manifests. `a4-builder` sets
`gateway_mode_ipv4: isolated` (Docker 28+): its bridge has no address, so a `RUN` cannot reach host listeners.

## Worker scanner binaries

The shared backend/worker image installs Gitleaks 8.30.1 and OSV-Scanner 2.7.0 during
build, using `scripts/install_a4_scanners.py` in the backend build context. Each Linux
amd64/arm64 asset has a reviewed literal SHA-256 pin; `TARGETARCH` selects the matching
asset. Unsupported architectures, checksum mismatches, unsafe archive members and
incorrect ELF architecture fail the build. Executables are installed with mode 0755;
upstream licenses are retained under `/usr/local/share/licenses/`.

No scanner download occurs when the worker starts. The existing scanner adapters still
control flags, platform-owned configuration, timeouts and rejection reasons. OSV package
vulnerability checks require network access; installing its binary does not provide an
offline vulnerability database or establish production connectivity.

`scripts/ci/smoke_a4_scanners.py` runs the actual adapters against temporary synthetic
fixtures inside the final image. Its default checks clean/no-package input and a fake
secret; `--online` additionally requires a known vulnerable dependency to be rejected.
An offline-only pass does not establish dependency vulnerability coverage. Run native
image acceptance for both target architectures; the standard production release build
remains native amd64. This packaging does not enable A4 or provision its worker service.

## Worker base-image policy

The worker reads `A4_BASE_IMAGE_DIGESTS` from trusted operator configuration as a JSON
array of exact lowercase `sha256:` digests with 64 hexadecimal characters. The default
is empty (`[]`): external base images are rejected; `scratch` and local build stages
remain supported. Invalid JSON, non-array values, or any invalid entry reject settings
initialization; entries are never trimmed, case-folded, or silently dropped. Duplicate
entries are deduplicated. Restart the worker after changing this setting.

`app.workers.a4.build_task()` passes the configured set to the existing Dockerfile
scanner. This does not replace the mirror gate's repository allowlist or permit tag-only
references. Operators must independently approve and provision real image digests for
the permitted repositories; this repository supplies no approved digest catalog. Do not
use synthetic test digests as deployment configuration or accept policy from agent code.

This interface does not provision the production worker, scanner binaries, builder
networks, or runtime. Those prerequisites and deployment acceptance remain separate;
`A4_DEPLOY_ENABLED` still defaults to false and `A4_RUNTIME` to `off`.

## Trust model

- Rootless BuildKit runs every `RUN` with `network.host`, in the daemon's own network namespace.
  A `RUN` therefore reaches every peer on `a4-builder`, including the `buildkitd` listener.
  The internal network is the real control, not build args or environment variables.
  Under runsc Docker's DNS refuses queries and a `RUN` has `resolv.conf` 8.8.8.8 and an empty
  `/etc/hosts`: peers are addressed by IP (`10.213.77.10` gate, `10.213.77.12` package mirrors). The listener requires mTLS: the client key lives only in the worker.
  The worker must open no listener on `a4-builder`.
- Rule for P3b: a `RUN` can `ptrace` the daemon, so anything handed to `buildkitd` is exposed to
  untrusted code. The worker passes only the build context: no registry auth, no secrets or ssh
  mounts, output `--output type=oci,dest=<file>`; the worker pushes the image itself.
- `--oci-worker-no-process-sandbox` lets a `RUN` kill or outlive daemon processes. The rule "one
  build per daemon" is a security rule: the worker restarts the daemon after every build (see Recycle).
- `a4-restart-gate` is root-equivalent access narrowed to two calls by `restart-gate.conf`. It is off
  `a4-builder` because a `RUN` shares buildkitd's network namespace and reaches every peer there;
  `a4-control` is `internal` and holds only the gate and `a4-worker`, which opens no listener.
  A general socket proxy with `containers` POST is refused: it allows creating privileged containers.
- With runsc's default `--host-uds=none` a unix socket created in the sandbox is not visible to the
  host, and changing that is a host-wide runtime flag, so no socket volume shared with the worker is
  used; mTLS over the internal network replaces it.
- `RUN` steps cannot use setuid binaries: the state tmpfs is mounted `exec` but keeps Docker's default `nosuid`, so a `RUN` that needs `sudo`, `su` or a setuid helper fails.

## mTLS material (once per host, outside the repository)

The CA key must not stay on the build host: generate in a temp dir, install the three server files,
move the client pair and `ca.pem` to the worker host, then shred everything else.

```bash
T=$(mktemp -d); cd "$T"
openssl req -x509 -newkey rsa:3072 -nodes -days 365 -subj /CN=a4-ca -keyout ca-key.pem -out ca.pem
openssl req -newkey rsa:3072 -nodes -subj /CN=buildkitd -keyout server-key.pem -out server.csr
printf 'subjectAltName=DNS:buildkitd\n' > san.cnf
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -days 365 -extfile san.cnf -out server.pem
openssl req -newkey rsa:3072 -nodes -subj /CN=a4-worker -keyout client-key.pem -out client.csr
openssl x509 -req -in client.csr -CA ca.pem -CAkey ca-key.pem -CAserial ca.srl -days 365 -out client.pem
D=/etc/a4-builder/tls; install -d -m 0755 "$D"
install -m 0444 ca.pem server.pem "$D"; install -m 0400 -o 1000 -g 1000 server-key.pem "$D"
# copy client.pem, client-key.pem, ca.pem to the worker host now, then:
shred -u ca-key.pem server-key.pem client-key.pem server.csr client.csr san.cnf ca.srl; cd /; rm -rf "$T"
export A4_BUILDKIT_CERT_DIR=$D
```

If you copy `tls/` to another path or host instead (`cp`, `rsync`, `scp`), repeat the ownership:
`sudo chown 1000:1000 server-key.pem && sudo chmod 0400 server-key.pem`. buildkitd runs as uid 1000
and reads the key through a Docker secret; any other owner or mode kills it at start with
`open /run/secrets/a4_buildkit_key: permission denied` (seen on the 31.10 smoke).

## Check gVisor on the host

```bash
docker info --format '{{json .Runtimes}}'   # must list "runsc"
runsc --version                              # release-20261012.0 on prod
ls -l /dev/kvm                               # present on prod: runsc platform kvm
docker run --rm --runtime=runsc busybox dmesg | head -3   # gVisor banner, not the host kernel log
```

Prod already has `runsc` registered in Docker 29.9.1; nothing to install. Without `runsc`,
`up` for `buildkitd` fails with `unknown or invalid runtime name: runsc`; do not substitute `runc`.

## Start, stop, recycle

```bash
C="docker compose -p a4-builder -f deploy/a4-builder.compose.yml"
$C up -d    # start (A4_BUILDKIT_CERT_DIR must be set)
$C ps
$C down -v          # stop, drop all state
```

Prerequisites for `up`: `export A4_DOCKER_GID=$(getent group docker | cut -d: -f3)`, and the
`a4-control` network must exist (the main compose file creates it when `a4-worker` starts, or run
`docker network create --internal a4-control`).

Recycle: the worker calls the gate after every build (success, failure, timeout, cancel):
`POST /v1.44/containers/a4-builder-buildkitd-1/restart?t=5`, then polls `GET .../json` until
`State.Running` and `State.Health.Status == healthy`. A restart discards the container's process
tree, network namespace and tmpfs state, so the next build starts on an empty daemon. If the restart
or the wait (90 s) fails, the worker marks the daemon dirty and refuses the next build with
`tool_failed` (logged) until a retry succeeds; a fresh worker treats the daemon as dirty too.
Manual recycle: `docker restart a4-builder-buildkitd-1`. The old anonymous state volume kept the
previous build's cache across `--force-recreate`; the tmpfs has no such volume.
Gate check (from a container on `a4-control`): `POST .../restart` 204, `GET .../json` 200,
`GET /v1.44/containers/json` 403, `POST /v1.44/containers/create` 403, `POST .../restart` with a body 413.

## Enable the BuildKit builder in the worker (preview, not operable yet)

Do not enable this on a live host before the stand check below. `buildctl` is in the backend image
(moby/buildkit v0.23.2, copied by digest) and the per-build recycle is implemented. Still missing:
- `a4-worker` is on `sporeai-network` and `a4-control`; `buildkitd` sits on the internal `a4-builder`
  network, so `A4_BUILDKIT_ADDR` has no route to it yet (a later step).
- Built tarballs are deleted right after the build; loading or pushing the image is a later step.
- Verify on the stand under runsc: the rootless snapshotter on the tmpfs state directory, the mTLS
  healthcheck (server pair used as client pair), `group_add` reaching the socket, the gate checks above.

The switch is `A4_BUILDER=buildkit` (default `fake`; roll back by unsetting it) with
`A4_BUILDKIT_CERT_DIR` (client `ca.pem`, `client.pem`, `client-key.pem`, mounted read-only),
`A4_BUILDKIT_ADDR` (default `tcp://buildkitd:1234`) and `A4_PKG_MIRROR_HOST`
(default `10.213.77.12`; a private IPv4 literal, settings refuse a hostname, loopback and public addresses; it must equal the `pkg-mirror` `ipv4_address` in `deploy/a4-builder.compose.yml` and the address literals in `deploy/a4-builder/pkg-mirror.conf` (`sub_filter`, `proxy_redirect`, the crates `config.json`): changing only the setting breaks downloads), `A4_RECYCLE_GATE_URL` (default `http://a4-restart-gate:2375`). Tarballs go to a 4 GiB tmpfs; the container limit is 6 GiB.
At start the worker refuses to run with `A4_BUILDER=buildkit` when `A4_BUILDKIT_ADDR` is not
`tcp://host:port`, a client TLS file (`ca.pem`, `client.pem`, `client-key.pem`) is missing or
unreadable, or `A4_BUILDER_OUT_DIR` is not a writable directory. The error names the setting or file, never contents.
A failed build shows the last 300 characters of `buildctl` stderr in the log, with `scheme://user:pass@`
credentials replaced by `://***@`.

When you run `buildctl` by hand, `GOSUMDB` holds a space, so it is one quoted argument:
`--opt 'build-arg:GOSUMDB=sum.golang.org http://10.213.77.12:8083/sumdb'` (unquoted, the shell splits it
and buildctl reads `http://...` as a separate argument). The worker already passes it as a single argv element.

The worker checks the tarball's `index.json` digest against buildkitd's and refuses on mismatch.

## Rollback

`$C down -v` removes the stack and its networks (`a4-builder`, `a4-mirror-back`, `a4-upstream`; `a4-control` belongs to the main compose file); revert the PR to remove the files.

## Smoke checks

Run a probe on the builder network (image already pinned in the compose file):

```bash
IMG=$($C config --images | grep nginx)
probe() { docker run --rm --network a4-builder --entrypoint sh "$IMG" -c "$1"; }
G=http://10.213.77.10:5000/v2/library/python/manifests   # mirror-gate
probe "wget -S -q -O /dev/null $G/sha256:<index digest> 2>&1 | grep HTTP"   # 200 (digest of a python tag)
probe "wget -S -q -O /dev/null $G/3.12-slim 2>&1 | grep HTTP"                # 403: tags refused
probe "wget -S -q -O /dev/null http://10.213.77.10:5000/v2/library/ubuntu/manifests/latest 2>&1 | grep HTTP"  # 403
# Package mirrors (pkg-mirror, 10.213.77.12); GET works, everything else is refused
M=10.213.77.12
probe "wget -S -q -O /dev/null http://$M:8081/simple/requests/ 2>&1 | grep HTTP"             # 200
probe "wget -q -O - http://$M:8081/simple/requests/ | grep -c files.pythonhosted.org"        # 0: links rewritten to $M
probe "wget -S -q -O /dev/null http://$M:8082/left-pad 2>&1 | grep HTTP"                     # 200
probe "wget -S -q -O /dev/null http://$M:8083/github.com/pkg/errors/@v/v0.9.1.info 2>&1 | grep HTTP"   # 200
probe "wget -S -q -O /dev/null http://$M:8084/3/a/arc 2>&1 | grep HTTP"                      # 200
probe "wget -S -q -O /dev/null --post-data=x http://$M:8084/api/v1/crates/new 2>&1 | grep HTTP"   # 405: no publish
probe "wget -S -q -O /dev/null http://$M:8081/legacy/ 2>&1 | grep HTTP"                      # 403: path not on the allowlist
probe "wget -S -q -O /dev/null --header='Host: evil.example' http://$M:8081/simple/requests/ 2>&1 | grep HTTP"  # 200: Host ignored
ip -4 addr show dev br-$(docker network inspect -f '{{printf "%.12s" .Id}}' a4-builder)  # nothing: bridge has no host address (.1 may belong to a container, so do not nc it)
ip -6 addr show dev br-$(docker network inspect -f '{{printf "%.12s" .Id}}' a4-builder)  # no global (inet6 scope global) address
probe 'wget -q -T 5 -O /dev/null http://1.1.1.1'                    # fails: Network unreachable
probe 'nc -z -w 3 buildkitd 1234 && echo open; echo | nc -w 3 buildkitd 1234 | wc -c'  # no plaintext answer
```

Static hardening is asserted by `python -m pytest backend/tests/test_a4_builder_compose.py backend/tests/test_a4_mirrors.py`
(no Docker needed). Validate the mirror config after any change:
`docker run --rm --tmpfs /tmp -v $PWD/deploy/a4-builder/pkg-mirror.conf:/etc/nginx/conf.d/default.conf:ro <pkg-mirror image> nginx -t`.

## Package mirrors (build authors)

`pkg-mirror` answers GET and HEAD only, for fixed upstream hosts (`pypi.org`, `files.pythonhosted.org`,
`registry.npmjs.org`, `proxy.golang.org`, `sum.golang.org`, `index.crates.io`, `static.crates.io`).
It forwards nothing the client sent (headers, cookies, query string, body), so a `Host` header cannot
pick another site and an upload is impossible. A `RUN` has no DNS under runsc: use the IPs.
Cached responses live 1 h; a package published in the last hour may not be visible yet.
The cache is a 2 GiB tmpfs (nginx keeps 1200 MB of entries and 500 MB free): an artifact above
500 MiB (some CUDA wheels) can fail with a truncated body, which pip reports as a hash mismatch.
Paths containing `..` are refused.

The worker passes these `--build-arg`s to every untrusted build, derived from `A4_PKG_MIRROR_HOST`
(default `10.213.77.12`, written below; it must equal the `pkg-mirror` `ipv4_address` in
`deploy/a4-builder.compose.yml` and the literals in `pkg-mirror.conf`: `sub_filter`, `proxy_redirect`
and the crates `config.json` still point at the old address if only the setting changes, and
downloads fail). No `HTTP_PROXY`/`HTTPS_PROXY` is passed, so no `NO_PROXY` is needed.

| Ecosystem | Build arg or file |
|---|---|
| pip | `PIP_INDEX_URL=http://10.213.77.12:8081/simple/`, `PIP_TRUSTED_HOST=10.213.77.12` |
| npm | `npm_config_registry=http://10.213.77.12:8082/`, `npm_config_audit=false` (audit is a POST, the mirror answers 405) |
| Go | `GOPROXY=http://10.213.77.12:8083`, `GOSUMDB="sum.golang.org http://10.213.77.12:8083/sumdb"` |
| cargo | no build arg: `.cargo/config.toml`, see below |

BuildKit exposes a non-predefined build arg to `RUN` only when the Dockerfile declares it with
`ARG NAME` in the stage that uses it, for example `ARG PIP_INDEX_URL` before `RUN pip install ...`.
An undeclared arg is dropped silently and the tool then tries the public internet, which a build
cannot reach. Declare every arg of the toolchain you use; `PIP_TRUSTED_HOST` is needed for pip
because the mirror speaks plain HTTP. Declared args are recorded in the image history; the internal
mirror address is not a secret, so this is accepted.

Cargo cannot replace a source from the environment (the cargo config reference marks
`source.<name>.replace-with` "Environment: not supported", checked 2026-10-31). `COPY` this file
into the image, in a parent directory of the directory where `cargo` runs or at
`$CARGO_HOME/config.toml`; a file that only sits in the build context is invisible to `RUN`:

```toml
[source.crates-io]
replace-with = "mirror"

[source.mirror]
registry = "sparse+http://10.213.77.12:8084/"
```

```dockerfile
WORKDIR /app
COPY .cargo/config.toml .cargo/config.toml
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release
```

Use the same address as `A4_PKG_MIRROR_HOST`. `GOFLAGS` is not set: modules resolve through
`GOPROXY` with the default `-mod` behaviour, and a vendored tree needs no mirror.

Residual risk after P3c: the request path still reaches the upstream host (a lookup of a package or
crate name that does not exist is a few bytes of one-way signal), and data is not scanned. Bulk
upload and Host-header or SNI tricks are gone. Registries that are not mirrored (git hosts, Maven,
RubyGems, apt) are unreachable from a `RUN`; add a mirror location when one is needed.

## a4-launcher (tenant runtime launcher)

`deploy/a4-launcher.compose.yml` runs the only service with the Docker socket for tenant runtimes
(`python -m app.launcher`, API on `a4-control` port 8080, no host ports).

The worker selector `A4_RUNTIME` defaults to `off`: a completed build still ends with
`runtime_unavailable`. Setting it to `launcher` connects the worker to the control API through
`A4_LAUNCHER_URL` (default `http://a4-launcher:8080`); the worker must share `a4-control` with the
launcher. Keep `A4_DEPLOY_ENABLED=false` for this release until the remaining runtime acceptance
checks and retirement/GC work are complete. The selector alone does not enable deployment.
To roll back runtime selection, restore `A4_RUNTIME=off` through the normal worker release process;
this prevents subsequent runtime launches but does not stop existing containers or revert routes.

```bash
export A4_DOCKER_GID=$(getent group docker | cut -d: -f3) A4_LAUNCHER_IMAGE=<backend image>
docker compose -p a4-launcher -f deploy/a4-launcher.compose.yml --profile a4 up -d
```

Settings (its own env, no DB/Redis/secrets): `A4_ATTACH` (containers joined to every tenant network,
default `agentspore-backend,agentspore-a4-worker`), `A4_TENANT_POOL` (default `10.214.0.0/16`, one /28 per
deployment; keep it clear of `10.213.0.0/16`), `A4_LAUNCHER_MAX_RUNTIMES` (default 20, 1..256: the launcher
refuses a new namespace with 429 `runtime_cap` once that many launcher-owned tenant networks exist; a repeat PUT of an
existing namespace is not counted, and it is a limit independent of the worker's `A4_MAX_ACTIVE`). Tenant runtimes need `runsc` registered in the Docker daemon.
Check from a container on `a4-control`: `GET http://agentspore-a4-launcher:8080/healthz` 200,
`GET /v1/runtimes` 200 `{"runtimes": []}`. Errors carry `{"reason": ...}` (`image_not_allowed`,
`digest_mismatch`, `image_volume_not_allowed`, `namespace_owned`, `ns_mismatch`, `runtime_cap`, `docker_error`; the daemon's text goes to the launcher log only).

## Known limits

- The 4 GiB build-disk cap is the `out_dir` volume limit, set in P3b-2; the builder enforces the 1 GiB image cap and the 10 minute timeout.
- `buildkitd` under `runsc` is not exercised by CI; check it on the stand before `A4_DEPLOY_ENABLED`.
- The mirror cache is a 4 GiB tmpfs (counted in the 5 GiB memory limit); registry v3 expires cached content after `proxy.ttl` (12 h; v2 keeps 7 days).
  When the cache is full or Docker Hub rate-limits, `mirror-gate` returns 5xx. Recycle: `$C up -d --force-recreate --no-deps mirror`; `$C logs --tail=50 mirror`.
