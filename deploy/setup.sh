#!/bin/bash
set -euo pipefail

# AgentsSpore — First-time server setup
# Run on a fresh DigitalOcean Droplet with Docker pre-installed
#
# Usage:
#   ssh root@<DROPLET_IP>
#   git clone https://github.com/AgentsSpore/agentspore.git /opt/agentspore
#   cd /opt/agentspore/deploy
#   cp .env.prod.example .env.prod
#   nano .env.prod  # fill in secrets
#   bash setup.sh

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"

echo "=== AgentsSpore Production Setup ==="
echo "Deploy dir: $DEPLOY_DIR"
echo "Repo dir:   $REPO_DIR"

# 1. Check .env.prod exists
if [ ! -f "$DEPLOY_DIR/.env.prod" ]; then
    echo "ERROR: .env.prod not found!"
    echo "Copy .env.prod.example to .env.prod and fill in values."
    exit 1
fi

# 2. Create swap (1GB RAM droplet needs swap)
if [ ! -f /swapfile ]; then
    echo ">>> Creating 2GB swap..."
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 3. Set up firewall
echo ">>> Configuring firewall..."
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP
ufw allow 443/tcp  # HTTPS
ufw --force enable

# 4. Start services
echo ">>> Starting services..."
cd "$DEPLOY_DIR"
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# 5. Wait and verify
echo ">>> Waiting for services to start..."
sleep 15

if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo ""
    echo "=== SUCCESS ==="
    echo "Backend is healthy!"
    echo ""
    echo "Next steps:"
    echo "  1. Add DNS A-record: agentspore.com -> $(curl -s ifconfig.me)"
    echo "  2. Wait for DNS propagation (~5 min)"
    echo "  3. Caddy will auto-obtain SSL certificate"
    echo "  4. Test: curl https://agentspore.com/health"
else
    echo "WARNING: Backend health check failed. Check logs:"
    echo "  docker compose -f docker-compose.prod.yml logs backend"
fi
