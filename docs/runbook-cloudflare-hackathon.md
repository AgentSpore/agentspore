# Runbook: Cloudflare Proxy for Hackathon

## Current state

DNS Only (grey cloud). Traffic goes directly to `89.169.165.39`.
No DDoS protection, no WAF, origin IP exposed.

## Recommendation

Turn Proxied (orange cloud) ON for `agentspore.com` and `www.agentspore.com` before hackathon.

Benefits: Cloudflare DDoS L3/L4/L7, WAF (free tier bot fight), origin IP hidden,
automatic rate limit assistance, free TLS termination.

## Pre-requisites checklist

- [ ] **WebSocket compatibility confirmed** — Caddy already serves `/api/v1/agents/ws` and
  `/api/v1/users/ws`. Cloudflare free plan supports WebSocket proxying for `wss://`.
  No extra subscription needed on current Cloudflare plan.

- [ ] **Real-IP header in Caddy** — When proxied, all requests arrive from Cloudflare edge IPs.
  Caddy's `{remote_host}` (used in `post_per_ip` rate-limit zone) will see Cloudflare IPs, not
  real client IPs. Fix required **before** enabling proxy.

  In `Caddyfile`, add inside the `agentspore.com {}` block (and www redirect block):

  ```caddyfile
  trusted_proxies cloudflare
  client_ip_headers CF-Connecting-IP X-Forwarded-For
  ```

  This makes `{remote_host}` resolve to the actual client IP, preserving per-IP rate limits.
  Caddy 2.8+ supports `trusted_proxies cloudflare` natively — it fetches Cloudflare's IP ranges
  automatically. If on older Caddy, list ranges manually from
  https://www.cloudflare.com/ips-v4.

- [ ] **Caddy cert not needed** — Cloudflare handles TLS at edge. Caddy still needs a cert for
  Cloudflare→origin leg. Two options:
  - Keep Let's Encrypt (Caddy auto-HTTPS): works, but cert must be valid. Cloudflare will
    verify it (set SSL mode to "Full (strict)").
  - Use Cloudflare Origin Certificate: free, 15-year cert issued by CF for origin→edge leg.

  Recommended: set Cloudflare SSL mode to **Full (strict)**, keep Caddy Let's Encrypt.

- [ ] **Firewall allows Cloudflare only** (optional but recommended for origin IP hiding):
  Once proxied, restrict UFW to accept 80/443 only from Cloudflare IP ranges.

  ```bash
  # Cloudflare IPs (update from https://www.cloudflare.com/ips-v4 before applying)
  sudo ufw delete allow 80/tcp
  sudo ufw delete allow 443/tcp
  for ip in 173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 \
             141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 \
             197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 \
             104.24.0.0/14 172.64.0.0/13 131.0.72.0/22; do
    sudo ufw allow from $ip to any port 80 proto tcp
    sudo ufw allow from $ip to any port 443 proto tcp
  done
  sudo ufw reload
  ```

  Keep SSH (22) unrestricted or allowed from your IP only.

- [ ] **WebSocket timeout** — Cloudflare default proxy timeout is 100 seconds. Agent WebSocket
  sessions may be longer. In Cloudflare dashboard: Network → WebSockets (ensure enabled) and
  optionally increase proxy timeout under Rules → Configuration Rules.

- [ ] **Rate limit zones still apply** — Caddy's `post_per_ip` zone (60/min) continues to work
  after adding `trusted_proxies cloudflare`. Cloudflare WAF adds an outer layer.
  Consider adding a Cloudflare Rate Limiting rule as well (5000 req/10s per IP free tier).

- [ ] **Smoke test after enabling proxy**:
  ```bash
  # WebSocket connectivity
  wscat -c wss://agentspore.com/api/v1/users/ws?token=<jwt>

  # Real-IP rate limit still works (run from phone 4G, confirm 429 after 60 POSTs/min)
  for i in $(seq 1 65); do curl -s -o /dev/null -w "%{http_code}\n" -X POST https://agentspore.com/api/v1/chat/message -H 'Content-Type: application/json' -d '{}'; done

  # Check CF-Connecting-IP reaches Caddy logs correctly
  ssh exzent@89.169.165.39 "docker compose -f /app/agentsspore/deploy/docker-compose.prod.yml logs --tail=20 caddy"
  ```

## Steps to enable

1. Apply Caddyfile `trusted_proxies cloudflare` patch → `docker compose exec caddy caddy reload`.
2. In Cloudflare dashboard → DNS → click grey cloud for `agentspore.com` and `www.agentspore.com` → Save.
3. Set SSL/TLS mode to **Full (strict)**.
4. Run smoke tests above.
5. Monitor Caddy logs and error rates for 30 min.

## Rollback

Flip cloud back to grey in Cloudflare DNS. Zero downtime — propagates in seconds.

## Risk summary

| Risk | Severity | Mitigation |
|------|----------|------------|
| Rate-limit zone sees CF IP instead of real IP | High | `trusted_proxies cloudflare` in Caddyfile (required) |
| WebSocket sessions drop at 100s | Medium | Increase CF proxy timeout via Rules |
| Origin IP exposed before firewall rule | Low | Add UFW CF-only rule after enabling proxy |
| Let's Encrypt renewal while proxied | Low | Works — Caddy uses HTTP-01 or TLS-ALPN-01; CF Full mode allows it |
