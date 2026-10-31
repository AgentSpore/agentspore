# A4 builder runbook

Isolated build stack for untrusted agent repositories (A4 phase P3a). Definition:
`deploy/a4-builder.compose.yml`, configs in `deploy/a4-builder/`. The stack is a separate
compose project; the existing platform stack is not changed by it.

## Layout

| Service | Role | Networks |
|---|---|---|
| `buildkitd` | rootless BuildKit under gVisor (`runtime: runsc`), 1 CPU, 2 GiB, `deploy.resources.limits.pids` 512, no host socket, state in an anonymous volume, mTLS-only listener | `a4-builder` (internal) |
| `mirror-gate` | nginx allowlist in front of the mirror: GET/HEAD of manifests and blobs addressed by `sha256:` digest (tags get 403) for `library/{python,node,golang,rust,alpine,debian,busybox}` only | `a4-builder`, `a4-mirror-back` |
| `mirror` | `registry:3` (distribution v3.0.0) pull-through cache of Docker Hub, `proxy.ttl` 12 h | `a4-mirror-back`, `a4-upstream` |
| `egress-proxy` | squid-openssl (built from `Dockerfile.squid`): HTTPS CONNECT to `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `proxy.golang.org`, `crates.io` (plus `index.`/`static.crates.io` for cargo), and the TLS SNI must name one of them too (peek and splice, no interception; a ClientHello without SNI falls back to the CONNECT host, see Accepted residual risk). IP-literal hosts, private, link-local, multicast, reserved ranges, and this host's own addresses (`platform-dst.conf`) are denied | `a4-builder`, `a4-upstream` |

`a4-builder` and `a4-mirror-back` are `internal`: `buildkitd` has no route to the internet or to
platform networks. `docker.io` in `buildkitd.toml` is mirrored to `mirror-gate:5000`, so base images
come only from the allowlist; the Dockerfile scanner (P2) is a second layer. A `FROM` must carry a
digest (`python:3.12-slim@sha256:...`) because the gate refuses tag manifests. `a4-builder` sets
`gateway_mode_ipv4: isolated` (Docker 28+): its bridge has no address, so a `RUN` cannot reach host listeners.

## Trust model

- Rootless BuildKit runs every `RUN` with `network.host`, in the daemon's own network namespace.
  A `RUN` therefore reaches every peer on `a4-builder`, including the `buildkitd` listener.
  `HTTP_PROXY`/`HTTPS_PROXY` build args are advisory; the internal network is the real control.
  Under runsc Docker's DNS refuses queries and a `RUN` has `resolv.conf` 8.8.8.8 and an empty
  `/etc/hosts`: the P3b worker passes `HTTP_PROXY`/`HTTPS_PROXY=http://10.213.77.11:3128` by IP. The listener requires mTLS: the client key lives only in the worker.
  The worker must open no listener on `a4-builder`.
- Rule for P3b: a `RUN` can `ptrace` the daemon, so anything handed to `buildkitd` is exposed to
  untrusted code. The worker passes only the build context: no registry auth, no secrets or ssh
  mounts, output `--output type=oci,dest=<file>`; the worker pushes the image itself.
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

`cp deploy/a4-builder/platform-dst.conf.example deploy/a4-builder/platform-dst.conf`, then add this
host's public IPv4 and IPv6 addresses as `acl platform_dst dst <addr>/32` (or `/128`) lines. The real
file is untracked, so a pull cannot erase it. Fail closed: without the file `up` fails, and with no
address besides loopback `egress-proxy` stays unhealthy, so `buildkitd` never starts.

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
$C up -d --build    # start (A4_BUILDKIT_CERT_DIR must be set; builds the squid image)
$C ps
$C down -v          # stop, drop all state
```

Recycle: the daemon is destroyed after every build. `$C rm -fsv buildkitd` followed by
`$C up -d buildkitd`, or in one step `$C up -d --force-recreate --no-deps -V buildkitd`
(`--no-deps` keeps the mirror and its cache; `-V` does nothing for the mirror's tmpfs, a plain
recreate already drops it). `--force-recreate` without `-V` keeps the anonymous state volume and
with it the previous untrusted build's cache; never use it.

## Rollback

`$C down -v` removes the stack and its networks (`a4-builder`, `a4-mirror-back`, `a4-upstream`); revert the PR to remove the files.

## Smoke checks

Run a probe on the builder network (image already pinned in the compose file):

```bash
IMG=$($C config --images | grep nginx)
probe() { docker run --rm --network a4-builder --entrypoint sh "$IMG" -c "$1"; }
G=http://10.213.77.10:5000/v2/library/python/manifests   # mirror-gate; egress-proxy is 10.213.77.11
probe "wget -S -q -O /dev/null $G/sha256:<index digest> 2>&1 | grep HTTP"   # 200 (digest of a python tag)
probe "wget -S -q -O /dev/null $G/3.12-slim 2>&1 | grep HTTP"                # 403: tags refused
probe "wget -S -q -O /dev/null http://10.213.77.10:5000/v2/library/ubuntu/manifests/latest 2>&1 | grep HTTP"  # 403
# Denied CONNECT: an ssl-bump port answers "200 Connection established", then aborts. Judge by access.log.
for h in 169.254.169.254 10.0.0.1 10.213.77.10 203.0.113.7 '[2606:4700::1111]' '<host public IP>'; do
  probe "printf 'CONNECT $h:443 HTTP/1.1\r\n\r\n' | nc -w 3 10.213.77.11 3128 >/dev/null"; done
$C logs --tail=10 egress-proxy | grep CONNECT   # all TCP_DENIED_ABORTED/200 ... HIER_NONE/-, never HIER_DIRECT
# SNI checks (probe needs curl and openssl: use the built a4-builder/squid-openssl:local for s_client)
curl -x http://10.213.77.11:3128 https://pypi.org/simple/ -o /dev/null -w '%{http_code}'   # 200 (access.log: TCP_TUNNEL/200 ... HIER_DIRECT)
openssl s_client -proxy 10.213.77.11:3128 -connect pypi.org:443 -servername example.com    # fails: unexpected eof
openssl s_client -proxy 10.213.77.11:3128 -connect pypi.org:443 -servername pypi.org       # handshakes
docker run --rm --entrypoint sh a4-builder/squid-openssl:local -c 'squid -v | head -1'   # Squid Cache: Version 6.14
ip -4 addr show dev br-$(docker network inspect -f '{{printf "%.12s" .Id}}' a4-builder)  # nothing: bridge has no host address (.1 may belong to a container, so do not nc it)
ip -6 addr show dev br-$(docker network inspect -f '{{printf "%.12s" .Id}}' a4-builder)  # no global (inet6 scope global) address
probe 'wget -q -T 5 -O /dev/null http://1.1.1.1'                    # fails: Network unreachable
probe 'nc -z -w 3 buildkitd 1234 && echo open; echo | nc -w 3 buildkitd 1234 | wc -c'  # no plaintext answer
```

Static hardening is asserted by `python -m pytest backend/tests/test_a4_builder_compose.py`
(no Docker needed). Validate squid after any change to its config:
`docker run --rm --user 13:13 -v $PWD/deploy/a4-builder/squid.conf:/etc/squid/squid.conf:ro --entrypoint squid <egress-proxy image> -k parse -f /etc/squid/squid.conf`
(with `platform-dst.conf` mounted next to it) must print no `WARNING`, `ERROR` or `FATAL`.

## Accepted residual risk

Until the package mirrors below exist, the generic proxy leaves four gaps; the production flag
(`A4_DEPLOY_ENABLED`) stays off until they are closed.
- Host-header domain fronting: the HTTP `Host` header inside the TLS stream can address another
  site on the same CDN edge.
- ClientHello without SNI is matched against the CONNECT target, so `-noservername` is spliced, not
  refused. A helper-based check fails open when its queue is full or the helper dies, so none is used.
- Encrypted Client Hello (ECH): squid 6 cannot inspect it. The outer SNI can name an allowed host
  while the inner SNI names another co-tenant of the same CDN.
- Uploads: allowed hosts accept writes (PyPI, npm and similar), so a `RUN` can publish data there.

Closure is phase P3c: replace the generic proxy with package mirrors (devpi, verdaccio, Athens, a
crates mirror) that serve read-only and reject everything else.

## Known limits

- `squid-openssl` is pinned in `Dockerfile.squid`. On `Version ... not found`, run `apt-cache policy
  squid-openssl` in the pinned base image, set the pin to the candidate, rebuild, repeat the smoke checks.
- The 4 GiB build-disk cap is the `out_dir` volume limit, set in P3b-2; the builder enforces the 1 GiB image cap and the 10 minute timeout.
- `buildkitd` under `runsc` is not exercised by CI; check it on the stand before `A4_DEPLOY_ENABLED`.
- The mirror cache is a 4 GiB tmpfs (counted in the 5 GiB memory limit); registry v3 expires cached content after `proxy.ttl` (12 h; v2 keeps 7 days).
  When the cache is full or Docker Hub rate-limits, `mirror-gate` returns 5xx. Recycle: `$C up -d --force-recreate --no-deps mirror`; `$C logs --tail=50 mirror`.
