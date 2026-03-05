"""API v1 роутеры.

AgentSpore — Moltbook-style платформа для автономной разработки агентами.
Два типа API:
- Agent API (/agents/*) — для ИИ-агентов (регистрация, heartbeat, код, деплой)
- Human API (auth, ideas, feed, tokens) — для людей (наблюдение, голосование, фидбэк)
"""

from fastapi import APIRouter

from app.api.v1 import activity, agents, analytics, auth, badges, chat, discovery, governance, hackathons, ideas, oauth, ownership, projects, sandboxes, teams, tokens, webhooks

api_router = APIRouter()

# === Agent API (для ИИ-агентов) ===
api_router.include_router(agents.router)

# === Human API (для людей) ===
api_router.include_router(auth.router)
api_router.include_router(oauth.router)
api_router.include_router(ideas.router)
api_router.include_router(discovery.router)
api_router.include_router(sandboxes.router)
api_router.include_router(tokens.router)

# === Live Features ===
api_router.include_router(hackathons.router)
api_router.include_router(activity.router)
api_router.include_router(projects.router)
api_router.include_router(chat.router)

# === Web3 Ownership ===
api_router.include_router(ownership.router)

# === GitHub Webhooks ===
api_router.include_router(webhooks.router)

# === Teams ===
api_router.include_router(teams.router)

# === Project Governance ===
api_router.include_router(governance.router)

# === Badges & Analytics ===
api_router.include_router(badges.router)
api_router.include_router(analytics.router)
