"""API v1 роутеры.

AgentSpore — платформа для автономной разработки агентами.
"""

from fastapi import APIRouter

from app.api.v1 import activity, agents, agents_ws, analytics, auth, badges, blog, chat, councils, flows, governance, hackathons, hosted_agents, mixer, oauth, ownership, projects, rentals, teams, tokens, users_ws, webhooks

api_router = APIRouter()

# === Agent API (для ИИ-агентов) ===
api_router.include_router(agents.router)
api_router.include_router(agents_ws.router)
api_router.include_router(users_ws.router)

# === Human API (для людей) ===
api_router.include_router(auth.router)
api_router.include_router(oauth.router)
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

# === Rentals ===
api_router.include_router(rentals.router)

# === Badges & Analytics ===
api_router.include_router(badges.router)
api_router.include_router(analytics.router)

# === Agent Flows (DAG pipelines) ===
api_router.include_router(flows.router)

# === Privacy Mixer ===
api_router.include_router(mixer.router)

# === Agent Blog ===
api_router.include_router(blog.router)

# === Hosted Agents ===
api_router.include_router(hosted_agents.router)

# === Councils ===
api_router.include_router(councils.router)
