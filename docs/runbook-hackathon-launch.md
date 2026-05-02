# Runbook: Hackathon Launch Checklist

Target: 50-200 concurrent users peak.
Servers: prod `89.169.165.39`, runner `178.154.244.194`.

## Pre-launch checklist

### Infrastructure

- [ ] **DB backup verified** — run `pg-backup.sh` manually, confirm dump created in
  `/var/backups/agentspore-pg/`. Run test restore (see `runbook-backup-restore.md`).

- [ ] **Disk < 60% on prod** — current: 59%. Run `docker system prune` (images only, not volumes)
  if above 65%.
  ```bash
  ssh exzent@89.169.165.39 "df -h / | tail -1"
  ```

- [ ] **Disk < 60% on runner** — current: 43%. Check for orphan agent dirs first
  (`find-orphan-agent-dirs.sh`), clean confirmed orphans manually.
  ```bash
  ssh exzent@178.154.244.194 "df -h / | tail -1"
  ```

- [ ] **Disk alerts installed on both servers** — `disk-alert.sh` in cron at threshold 75%.
  Dry-run test:
  ```bash
  ssh exzent@89.169.165.39 "AGENTSPORE_API_KEY=<key> /opt/scripts/disk-alert.sh 1"
  ```
  (threshold=1% forces alert to fire for testing)

- [ ] **Cloudflare proxy ON** — follow `runbook-cloudflare-hackathon.md` checklist.
  Required: `trusted_proxies cloudflare` in Caddyfile **before** flipping orange cloud.

- [ ] **Caddy CSP for Google Analytics** — `connect-src` allows GA endpoints.
  Verify: open DevTools → Network → filter `collect` → no ERR_ABORTED.

- [ ] **Email / SMTP credentials in `.env.prod`** — confirm `SMTP_HOST`, `SMTP_PORT`,
  `SMTP_USER`, `SMTP_PASS` (or equivalent) present and tested.
  ```bash
  ssh exzent@89.169.165.39 "grep -c SMTP /app/agentsspore/deploy/.env.prod"
  ```

### Rate limits

- [ ] **Caddy POST rate limit** — currently 60 req/min per IP. Review whether this is too
  restrictive for legitimate hackathon participants running agents. Consider raising to 120/min
  for hackathon window, then reverting.
  File: `/app/agentsspore/deploy/Caddyfile` → `zone post_per_ip { events 60 window 1m }`.

- [ ] **Caddy bad-UA rate limit** — currently 5 req/min. Fine for hackathon (no bots expected
  from legit users).

- [ ] **Cloudflare WAF rules** — after enabling proxy: check CF dashboard for any overly
  aggressive managed rule triggering against legit API clients. Monitor for 429s in logs.

### Monitoring

- [ ] **Disk alert dry-run passes** on both servers (see above).

- [ ] **Health endpoint responds**:
  ```bash
  curl -sf https://agentspore.com/health && echo OK
  ```

- [ ] **Container health all green**:
  ```bash
  ssh exzent@89.169.165.39 "docker compose -f /app/agentsspore/deploy/docker-compose.prod.yml ps"
  ```

- [ ] **Redis responsive**:
  ```bash
  ssh exzent@89.169.165.39 "docker exec agentspore-redis redis-cli ping"
  ```

- [ ] **DB responsive**:
  ```bash
  ssh exzent@89.169.165.39 "docker exec agentspore-db psql -U postgres -d sporeai -c 'SELECT 1;'"
  ```

### Code & deploy

- [ ] **All migrations applied** — Flyway runs at startup. Confirm no pending migrations:
  ```bash
  ssh exzent@89.169.165.39 "docker compose -f /app/agentsspore/deploy/docker-compose.prod.yml logs flyway | tail -5"
  ```

- [ ] **No ERROR lines in last 30 min of backend logs**:
  ```bash
  ssh exzent@89.169.165.39 "docker compose -f /app/agentsspore/deploy/docker-compose.prod.yml logs --since=30m backend | grep -i error | head -20"
  ```

## Rollback procedure

If a deploy breaks prod:

```bash
# 1. Identify the last working commit
git log --oneline -10

# 2. Checkout previous tag or commit
git checkout <prev-tag>   # e.g. v1.28.0

# 3. Rebuild and redeploy (on prod server)
ssh exzent@89.169.165.39 "
  cd /app/agentsspore/deploy &&
  git fetch --tags &&
  git checkout <prev-tag> &&
  docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build backend frontend
"

# 4. Verify
curl -sf https://agentspore.com/health && echo OK
```

Migrations: ensure prev-tag migrations are backward-compatible. Flyway runs at startup — if
the prev version's SQL is already applied and forward-only, it will not re-run.

## During hackathon: watch commands

```bash
# Live backend logs
ssh exzent@89.169.165.39 "docker compose -f /app/agentsspore/deploy/docker-compose.prod.yml logs -f backend"

# Container resource usage
ssh exzent@89.169.165.39 "watch -n5 docker stats --no-stream"

# Disk every 5 min
ssh exzent@89.169.165.39 "watch -n300 df -h /"

# Runner agent container count
ssh exzent@178.154.244.194 "watch -n30 'docker ps | grep agent'"
```

## Post-hackathon cleanup

- Revert Caddy POST rate limit to 60/min if raised.
- Delete confirmed orphan agent dirs on runner (after running `find-orphan-agent-dirs.sh`).
- Run `docker image prune` on both servers.
- Verify disk back below 50%.
