"""Fixed-destination publication transport owned by the trusted controller."""

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx


class OperatorPublisher:
    """Publish only after the caller commits a ledger reserve and checks schedule."""

    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        """Keep the key in the trusted process, never in model input or logs."""
        if (
            not key
            or not key.isascii()
            or any(character.isspace() for character in key)
        ):
            raise ValueError("Invalid publication credential")
        self._client = client
        self._key = key

    @staticmethod
    def _validate(channel: str, payload: dict[str, Any]) -> str:
        if channel not in ("chat", "blog") or not isinstance(payload, dict):
            raise ValueError("Unknown publication operation")
        fields = (
            {"message_type", "content"} if channel == "chat" else {"title", "content"}
        )
        if set(payload) != fields:
            raise ValueError("Unexpected publication fields")
        limits = (
            {"content": 2000, "message_type": 4}
            if channel == "chat"
            else {"title": 300, "content": 50000}
        )
        for name, limit in limits.items():
            value = payload[name]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError("Invalid publication field")
        if channel == "chat" and payload["message_type"] != "text":
            raise ValueError("Only plain text chat publication is permitted")
        return "https://agentspore.com/api/v1/" + (
            "chat/message" if channel == "chat" else "blog/posts"
        )

    @staticmethod
    def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate response fields")
        return result

    @classmethod
    def _receipt(cls, content: bytes | bytearray, channel: str) -> str | None:
        body = json.loads(content, object_pairs_hook=cls._object)
        fields = (
            {"status", "message_id"}
            if channel == "chat"
            else {"id", "agent_id", "title", "created_at"}
        )
        if not isinstance(body, dict) or set(body) != fields:
            return None
        if channel == "chat" and body["status"] != "ok":
            return None
        receipt = body["message_id" if channel == "chat" else "id"]
        if not isinstance(receipt, str) or len(receipt) != 36:
            return None
        return receipt if str(UUID(receipt)) == receipt else None

    async def publish(self, channel: str, payload: dict[str, Any]) -> str | None:
        """Attempt once with 14 seconds of work and at most one second of cleanup.

        Transport cancellation must be cooperative. None means uncertain delivery
        and forbids automatic retry; cleanup never runs as a background task.
        """
        url = self._validate(channel, payload)
        request = httpx.Request(
            "POST",
            url,
            json=payload,
            headers={
                "X-API-Key": self._key,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            extensions={
                "timeout": {name: 10.0 for name in ("connect", "read", "write", "pool")}
            },
        )
        try:
            async with asyncio.timeout(14):
                response = await self._client.send(
                    request, stream=True, auth=None, follow_redirects=False
                )
                try:
                    if response.status_code != (200 if channel == "chat" else 201):
                        return None
                    if (
                        response.headers.get("content-encoding", "identity")
                        != "identity"
                    ):
                        return None
                    content = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        content.extend(chunk)
                        if len(content) > 65536:
                            return None
                    return self._receipt(content, channel)
                finally:
                    async with asyncio.timeout(1):
                        await response.aclose()
        except (httpx.HTTPError, TimeoutError, ValueError, RecursionError):
            return None
