# Runbook: Sandbox network isolation (C3)

## Problem

Default Docker bridge network (`docker0 / 172.17.0.0/16`) lets sandbox containers
reach `172.17.0.1` — the host gateway. From there they can hit:
- Backend on `localhost:8000`
- Runner itself on `localhost:8100`
- DB if bound to `0.0.0.0` (Postgres default)

This is a Server-Side Request Forgery (SSRF) path from untrusted agent code.

## Fix: dedicated isolated bridge + host iptables

Sandbox containers are spawned into a custom bridge network (`sandbox_net`).
Host iptables rules in the `DOCKER-USER` chain drop traffic from that subnet to
all RFC1918 ranges, blocking SSRF to internal services while leaving public
internet (LLM APIs) reachable.

## One-time deploy steps (run as root on 178.154.244.194)

### 1. Create the sandbox network

```bash
docker network create \
  --driver bridge \
  --subnet=10.99.0.0/16 \
  --opt com.docker.network.bridge.name=sandbox_net \
  sandbox_net
```

Verify:
```bash
docker network inspect sandbox_net | grep -E '"Subnet"|"Gateway"'
# expect: "Subnet": "10.99.0.0/16", "Gateway": "10.99.0.1"
```

### 2. Add iptables rules (DOCKER-USER chain)

Docker inserts `DOCKER-USER` before its own rules — this is the correct hook for
operator-level policy that Docker itself will not override.

```bash
# Drop RFC1918 destinations from the sandbox subnet.
# Order matters: RETURN rules for legitimate destinations go first,
# then the DROP catchall for private ranges.

# Allow traffic leaving sandbox_net to public internet (no restriction needed —
# the DROP rules below only match private ranges).

# Block sandbox → loopback (catches host-networked services)
iptables -I DOCKER-USER -s 10.99.0.0/16 -d 127.0.0.0/8 -j DROP

# Block sandbox → link-local (AWS/GCP/Azure IMDS — metadata service)
iptables -I DOCKER-USER -s 10.99.0.0/16 -d 169.254.0.0/16 -j DROP

# Block sandbox → RFC1918 class A
iptables -I DOCKER-USER -s 10.99.0.0/16 -d 10.0.0.0/8 -j DROP

# Block sandbox → RFC1918 class B (includes default docker0 bridge 172.17/16)
iptables -I DOCKER-USER -s 10.99.0.0/16 -d 172.16.0.0/12 -j DROP

# Block sandbox → RFC1918 class C
iptables -I DOCKER-USER -s 10.99.0.0/16 -d 192.168.0.0/16 -j DROP
```

**Important:** The 10.99.0.0/16 block covers sandbox→sandbox inter-container
traffic too (they share the subnet). This is intentional — agents must not talk
to each other directly.

Verify rules are in place:
```bash
iptables -L DOCKER-USER -n --line-numbers | grep 10.99
```

### 3. Persist across reboots (Debian/Ubuntu)

```bash
apt-get install -y iptables-persistent
netfilter-persistent save
```

Or add to `/etc/rc.local` / a systemd `ExecStartPost` unit before `docker.service`.

### 4. Rebuild and redeploy the runner

```bash
cd /app/agentsspore/agent-runner
docker build -t agentspore-sandbox:latest -f Dockerfile.sandbox .
docker compose -f ../deploy/docker-compose.prod.yml restart runner
```

### 5. Smoke test

From outside:
```bash
curl http://178.154.244.194:8100/health
```

From inside a running sandbox container (should time out/refuse):
```bash
# Get container id of a running agent
CNAME=$(docker ps --filter ancestor=agentspore-sandbox:latest -q | head -1)
docker exec $CNAME curl -m3 http://172.17.0.1:8000/health  # expect: no route / timeout
docker exec $CNAME curl -m3 https://openrouter.ai/api/v1/models  # expect: 401 (reaches internet)
```

## Rollback

```bash
iptables -D DOCKER-USER -s 10.99.0.0/16 -d 127.0.0.0/8 -j DROP
iptables -D DOCKER-USER -s 10.99.0.0/16 -d 169.254.0.0/16 -j DROP
iptables -D DOCKER-USER -s 10.99.0.0/16 -d 10.0.0.0/8 -j DROP
iptables -D DOCKER-USER -s 10.99.0.0/16 -d 172.16.0.0/12 -j DROP
iptables -D DOCKER-USER -s 10.99.0.0/16 -d 192.168.0.0/16 -j DROP
docker network rm sandbox_net
# Set SANDBOX_NETWORK_NAME= (empty) in .env.prod to fall back to default bridge
```

## Configuration

The runner reads `SANDBOX_NETWORK_NAME` (default: `sandbox_net`).
To disable network isolation during local dev (no `sandbox_net` exists):

```bash
SANDBOX_NETWORK_NAME=  # empty → Docker default bridge
```

Or in `docker-compose.yml`:
```yaml
- SANDBOX_NETWORK_NAME=
```

## Why not --internal?

`--internal` drops all external egress, blocking LLM API calls. Option C
(egress proxy) is more robust but requires extra infrastructure. Option B
(iptables host rules) is the right hackathon-scoped choice: zero new services,
effective SSRF block, LLM APIs still reachable via public IPs.
