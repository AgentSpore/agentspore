"""Preserve global chat history DTOs through the synchronous HTTP client."""

from dataclasses import asdict
from typing import TypedDict

import httpx
import pytest
from agentspore import AgentSpore
from agentspore.exceptions import APIError, AuthError
from agentspore.types import ChatMessage

LEGACY_MESSAGE = {
    "id": "11111111-1111-4111-8111-111111111111",
    "content": "Synthetic history message",
    "agent_name": "Synthetic sender",
    "message_type": "text",
    "ts": "2026-10-20 09:00:00+00:00",
}
METADATA_DEFAULTS = {
    "sender_type": None,
    "agent_id": None,
    "specialization": None,
    "sender_user_id": None,
    "is_deleted": False,
    "edited_at": None,
}


class ChatResponse(TypedDict):
    status_code: int
    json: object


@pytest.fixture
def chat_client():
    response: ChatResponse = {"status_code": 200, "json": []}

    def respond(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v1/chat/messages"
        assert dict(request.url.params) == {"limit": "17"}
        assert request.headers["X-API-Key"] == "synthetic"
        return httpx.Response(response["status_code"], json=response["json"])

    with AgentSpore("synthetic", base_url="https://platform.invalid") as client:
        headers = client._client.headers.copy()
        client._client.close()
        client._client = httpx.Client(
            base_url=client.base_url,
            headers=headers,
            transport=httpx.MockTransport(respond),
        )
        yield client, response


@pytest.fixture(
    params=[
        {"sender_user_id": "22222222-2222-4222-8222-222222222222"},
        {},
        {
            "sender_type": "agent",
            "agent_id": "33333333-3333-4333-8333-333333333333",
            "specialization": "programmer",
        },
        {"edited_at": "2026-10-20 09:05:00+00:00"},
        {
            "content": "[deleted]",
            "is_deleted": True,
            "edited_at": "2026-10-20 09:10:00+00:00",
        },
        None,
    ],
    ids=[
        "owned-user",
        "legacy-null-owner",
        "agent",
        "edited",
        "deleted",
        "five-fields",
    ],
)
def history_message(request):
    # Shape from ChatRepository.get_recent_messages; all values are synthetic.
    if request.param is None:
        return LEGACY_MESSAGE.copy()
    return {
        **LEGACY_MESSAGE,
        "sender_type": "user",
        "agent_id": None,
        "specialization": "human",
        "sender_user_id": None,
        "is_deleted": False,
        **request.param,
    }


def test_history_dtos_preserve_all_metadata(chat_client, history_message):
    client, response = chat_client
    response["json"] = [history_message]

    messages = client.get_chat_messages(limit=17)

    assert len(messages) == 1
    assert isinstance(messages[0], ChatMessage)
    assert asdict(messages[0]) == {**METADATA_DEFAULTS, **history_message}


def test_empty_history_returns_empty_list(chat_client):
    client, _ = chat_client

    assert client.get_chat_messages(limit=17) == []


@pytest.mark.parametrize(
    ("status_code", "exception", "message"),
    [
        (401, AuthError, "Invalid or missing API key"),
        (503, APIError, "API error 503: Synthetic service unavailable"),
    ],
    ids=["unauthorized", "service-unavailable"],
)
def test_http_errors_raise_sdk_exceptions_before_dto_parsing(
    chat_client, status_code, exception, message
):
    client, response = chat_client
    response.update(
        status_code=status_code,
        json={"detail": "Synthetic service unavailable"},
    )

    with pytest.raises(exception, match=message) as error:
        client.get_chat_messages(limit=17)

    if status_code == 503:
        assert error.value.status_code == 503
        assert error.value.detail == "Synthetic service unavailable"


def test_legacy_positional_constructor_accepts_five_fields():
    message = ChatMessage(
        LEGACY_MESSAGE["id"],
        LEGACY_MESSAGE["content"],
        LEGACY_MESSAGE["agent_name"],
        LEGACY_MESSAGE["message_type"],
        LEGACY_MESSAGE["ts"],
    )

    assert asdict(message) == {**LEGACY_MESSAGE, **METADATA_DEFAULTS}
