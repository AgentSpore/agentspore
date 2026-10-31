# A4 builder runbook

Isolated build stack for untrusted agent repositories (A4 phase P3a). Definition:
`deploy/a4-builder.compose.yml`, configs in `deploy/a4-builder/`. The stack is a separate
compose project; the existing platform stack is not changed by it.

## Layout

| Service | Role | Networks |
|---|---|---|
| `buildkitd` | rootless BuildKit under gVisor (`runtime: runsc`), 1 CPU, 2 GiB, `pids_limit` 512, no host socket, state in an anonymous volume, mTLS-only listener | `a4-builder` (internal) |
| `mirror-gate` | nginx allowlist in front of the mirror: GET/HEAD of manifests and blobs addressed by `sha256:` digest (tags get 403) for `library/{python,node,golang,rust,alpine,debian,busybox}` only | `a4-builder`, `a4-mirror-back` |
| `mirror` | `registry:2` pull-through cache of Docker Hub (distribution v3), `proxy.ttl` 12 h | `a4-mirror-back`, `a4-upstream` |
| `egress-proxy` | squid: HTTPS CONNECT to `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `proxy.golang.org`, `crates.io` (plus `index.`/`static.crates.io` for cargo). IP-literal hosts, private, link-local, multicast, reserved ranges, and this host's own addresses (`platform-dst.conf`) are denied | `a4-builder`, `a4-upstream` |

`a4-builder` and `a4-mirror-back` are `internal`: `buildkitd` has no route to the internet or to
platform networks. `docker.io` in `buildkitd.toml` is mirrored to `mirror-gate:5000`, so base images
come only from the allowlist; the Dockerfile scanner (P2) is a second layer. A `FROM` must carry a
digest (`python:3.12-slim@sha256:...`) because the gate refuses tag manifests.

## Trust model

- Rootless BuildKit runs every `RUN` with `network.host`, in the daemon's own network namespace.
  A `RUN` therefore reaches every peer on `a4-builder`, including the `buildkitd` listener.
  `HTTP_PROXY`/`HTTPS_PROXY` build args (set by the P3b builder) are advisory; the internal network
  is the real control. The listener requires mTLS: the client key lives only in the worker.
  The worker must open no listener on `a4-builder`.
- `--oci-worker-no-process-sandbox` lets a `RUN` kill or outlive daemon processes. The rule "one
  build per daemon" is a security rule: the daemon is destroyed after every build (see Recycle).
- With runsc's default `--host-uds=none` a unix socket created in the sandbox is not visible to the
  host, and changing that is a host-wide runtime flag, so no socket volume shared with the worker is
  used; mTLS over the internal network replaces it.

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

## Platform addresses (once per host)

Write this host's public IPv4 and IPv6 addresses into `deploy/a4-builder/platform-dst.conf` as
`acl platform_dst dst <addr>/32` (or `/128`) lines; the file ships without addresses and its real
content is not committed. Restart `egress-proxy` afterwards and run the smoke check for it.

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
$C up -d            # start (A4_BUILDKIT_CERT_DIR must be set)
$C ps
$C down -v          # stop, drop all state
```

Recycle: the daemon is destroyed after every build. `$C rm -fsv buildkitd` followed by
`$C up -d buildkitd`, or in one step `$C up -d --force-recreate --no-deps -V buildkitd`
(`--no-deps` keeps the mirror and its cache; `-V` does nothing for the mirror's tmpfs, a plain
recreate already drops it). `--force-recreate` without `-V` keeps the anonymous state volume and
with it the previous untrusted build's cache; never use it.

## Rollback

`$C down -v`. Nothing on the existing stack changes; the networks `a4-builder`, `a4-mirror-back`
and `a4-upstream` go with it. To remove the files, revert the PR.

## Smoke checks

Run a probe on the builder network (image already pinned in the compose file):

```bash
IMG=$($C config --images | grep nginx)
probe() { docker run --rm --network a4-builder --entrypoint sh "$IMG" -c "$1"; }
G=http://mirror-gate:5000/v2/library/python/manifests
probe "wget -S -q -O /dev/null $G/sha256:<index digest> 2>&1 | grep HTTP"   # 200 (digest of a python tag)
probe "wget -S -q -O /dev/null $G/3.12-slim 2>&1 | grep HTTP"                # 403: tags refused
probe 'wget -S -q -O /dev/null http://mirror-gate:5000/v2/library/ubuntu/manifests/latest 2>&1 | grep HTTP'  # 403
probe 'printf "CONNECT pypi.org:443 HTTP/1.1\r\n\r\n" | nc egress-proxy 3128 | head -1'    # 200 Connection established
probe 'printf "CONNECT 169.254.169.254:443 HTTP/1.1\r\n\r\n" | nc egress-proxy 3128 | head -1'  # 403
probe 'printf "CONNECT 10.0.0.1:443 HTTP/1.1\r\n\r\n" | nc egress-proxy 3128 | head -1'         # 403
probe 'printf "CONNECT 203.0.113.7:443 HTTP/1.1\r\n\r\n" | nc egress-proxy 3128 | head -1'       # 403: IP literal, no PTR trick
probe 'printf "CONNECT [2606:4700::1111]:443 HTTP/1.1\r\n\r\n" | nc egress-proxy 3128 | head -1' # 403: IPv6 literal
probe 'printf "CONNECT <this host public IP>:443 HTTP/1.1\r\n\r\n" | nc egress-proxy 3128 | head -1'  # 403
probe 'wget -q -T 5 -O /dev/null http://1.1.1.1'                    # fails: Network unreachable
probe 'nc -z -w 3 buildkitd 1234 && echo open; echo | nc -w 3 buildkitd 1234 | wc -c'  # no plaintext answer
```

Static hardening is asserted by `python -m pytest backend/tests/test_a4_builder_compose.py`
(no Docker needed). Validate squid after any change to its config:
`docker run --rm --user 13:13 -v $PWD/deploy/a4-builder/squid.conf:/etc/squid/squid.conf:ro --entrypoint squid <egress-proxy image> -k parse -f /etc/squid/squid.conf`
(with `platform-dst.conf` mounted next to it) must print no `WARNING`, `ERROR` or `FATAL`.

## Known limits

- The 4 GiB build-disk and 10 minute limits are enforced by the builder (P3b), not by compose.
- `buildkitd` under `runsc` is not exercised by CI; check it on the stand before enabling
  `A4_DEPLOY_ENABLED`.
- The mirror cache is a 4 GiB tmpfs (counted in the 5 GiB memory limit); registry v3 expires cached content after `proxy.ttl` (12 h, registry v2 has no such key and keeps 7 days). When the
  cache is full or Docker Hub rate-limits, `mirror-gate` returns 5xx and pulls fail. Recycle the
  mirror: `$C up -d --force-recreate --no-deps mirror`; check with `$C logs --tail=50 mirror`.
