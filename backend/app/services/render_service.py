"""Render.com API client — auto-deploy projects as static sites or web services."""

import re
from typing import Any

import httpx

from loguru import logger


class RenderError(Exception):
    """Ошибка при вызове Render API."""


class RenderService:
    """Клиент для Render API v1.

    Создаёт Static Sites (React/Vite) или Web Services (Python API)
    из GitHub репозиториев AgentSpore.
    """

    BASE_URL = "https://api.render.com/v1"

    def __init__(self, api_key: str, owner_id: str) -> None:
        self.api_key = api_key
        self.owner_id = owner_id
        self._client = httpx.AsyncClient(timeout=60.0)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Service creation
    # ------------------------------------------------------------------

    async def create_static_site(
        self,
        name: str,
        repo_url: str,
        branch: str = "main",
        build_command: str = "npm install && npm run build",
        publish_dir: str = "build",
    ) -> dict[str, Any]:
        """Создать Static Site на Render из GitHub repo."""
        payload = {
            "type": "static_site",
            "name": name,
            "ownerId": self.owner_id,
            "repo": repo_url,
            "autoDeploy": "yes",
            "branch": branch,
            "serviceDetails": {
                "buildCommand": build_command,
                "publishPath": publish_dir,
            },
        }
        resp = await self._client.post(
            f"{self.BASE_URL}/services",
            json=payload,
            headers=self._headers,
        )
        if not resp.is_success:
            raise RenderError(f"create_static_site failed: {resp.status_code} {resp.text}")
        data = resp.json()
        service = data.get("service", data)
        logger.info(
            "Created static site '%s' → %s",
            name,
            service.get("serviceDetails", {}).get("url", "pending"),
        )
        return service

    async def create_web_service(
        self,
        name: str,
        repo_url: str,
        branch: str = "main",
        build_command: str = "npm install",
        start_command: str = "npm start",
        runtime: str = "node",
    ) -> dict[str, Any]:
        """Создать Web Service на Render из GitHub repo."""
        payload = {
            "type": "web_service",
            "name": name,
            "ownerId": self.owner_id,
            "repo": repo_url,
            "autoDeploy": "yes",
            "branch": branch,
            "serviceDetails": {
                "env": runtime,
                "buildCommand": build_command,
                "startCommand": start_command,
                "plan": "free",
                "region": "oregon",
            },
        }
        resp = await self._client.post(
            f"{self.BASE_URL}/services",
            json=payload,
            headers=self._headers,
        )
        if not resp.is_success:
            raise RenderError(f"create_web_service failed: {resp.status_code} {resp.text}")
        data = resp.json()
        service = data.get("service", data)
        logger.info(
            "Created web service '%s' → %s",
            name,
            service.get("serviceDetails", {}).get("url", "pending"),
        )
        return service

    # ------------------------------------------------------------------
    # Service management
    # ------------------------------------------------------------------

    async def get_service(self, service_id: str) -> dict[str, Any]:
        """Получить информацию о сервисе."""
        resp = await self._client.get(
            f"{self.BASE_URL}/services/{service_id}",
            headers=self._headers,
        )
        if not resp.is_success:
            raise RenderError(f"get_service failed: {resp.status_code}")
        return resp.json()

    async def find_service_by_name(self, name: str) -> dict[str, Any] | None:
        """Найти сервис по имени (чтобы не создавать дубли)."""
        resp = await self._client.get(
            f"{self.BASE_URL}/services",
            params={"name": name, "limit": 1},
            headers=self._headers,
        )
        if not resp.is_success:
            return None
        services = resp.json()
        if services:
            return services[0].get("service", services[0])
        return None

    async def trigger_deploy(self, service_id: str) -> dict[str, Any]:
        """Тригернуть ре-деплой существующего сервиса."""
        resp = await self._client.post(
            f"{self.BASE_URL}/services/{service_id}/deploys",
            json={},
            headers=self._headers,
        )
        if not resp.is_success:
            raise RenderError(f"trigger_deploy failed: {resp.status_code}")
        return resp.json()

    # ------------------------------------------------------------------
    # High-level deploy
    # ------------------------------------------------------------------

    @staticmethod
    def _slug(title: str) -> str:
        """Из названия проекта сделать slug для Render сервиса."""
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return slug[:50] or "sporeai-project"

    async def deploy_project(
        self,
        repo_url: str,
        title: str,
    ) -> dict[str, str]:
        """Задеплоить проект на Render.

        Определяет тип сервиса (static_site для React, web_service для API).
        Если сервис с таким именем уже существует — тригерит ре-деплой.

        Returns:
            {"service_id": "srv-...", "deploy_url": "https://name.onrender.com"}
        """
        name = self._slug(title)

        # Проверяем, не создан ли уже сервис с таким именем
        existing = await self.find_service_by_name(name)
        if existing:
            service_id = existing["id"]
            deploy_url = existing.get("serviceDetails", {}).get("url", "")
            logger.info("Service '%s' already exists (id=%s), triggering re-deploy", name, service_id)
            try:
                await self.trigger_deploy(service_id)
            except RenderError as e:
                logger.warning("Re-deploy trigger failed: %s", e)
            return {"service_id": service_id, "deploy_url": deploy_url}

        # Создаём Static Site (подходит для React/CRA apps — бесплатно, не засыпает)
        service = await self.create_static_site(
            name=name,
            repo_url=repo_url,
            build_command="npm install && npm run build",
            publish_dir="build",
        )

        service_id = service["id"]
        deploy_url = service.get("serviceDetails", {}).get("url", f"https://{name}.onrender.com")

        return {"service_id": service_id, "deploy_url": deploy_url}
