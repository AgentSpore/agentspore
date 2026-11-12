"""Telegram Bot API sender. The token lives in one private attribute; the URL carrying it is
built inside `send` and never reaches a log line or an exception message."""

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path


class SendError(Exception):
    """A failed send: HTTP status, server-requested wait and exception class name only."""

    def __init__(self, status: int | None, retry_after: float | None, kind: str) -> None:
        super().__init__(f"{kind} status={status}")
        self.status, self.retry_after, self.kind = status, retry_after, kind


def read_token() -> str:
    """Bot token from the systemd credential `bot-token`."""
    return (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "bot-token").read_text().strip()


class TelegramSender:
    """Sends text to one chat; holds the bot token privately."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        prefix: str = "",
        opener: Callable = urllib.request.urlopen,
        timeout: float = 10.0,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._prefix = prefix
        self._opener = opener
        self._timeout = timeout

    def send(self, text: str) -> None:
        """POST one message; raises SendError unless Telegram answers `ok`."""
        body = f"{self._prefix} {text}".strip()
        data = urllib.parse.urlencode({"chat_id": self._chat_id, "text": body}).encode()
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        request = urllib.request.Request(url, data=data, method="POST")  # noqa: S310 (url is the fixed https://api.telegram.org prefix, scheme cannot vary)
        try:
            with self._opener(request, timeout=self._timeout) as response:
                answer = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise SendError(exc.code, _retry_after(exc), type(exc).__name__) from None
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise SendError(None, None, type(exc).__name__) from None
        if not (isinstance(answer, dict) and answer.get("ok") is True):
            raise SendError(None, None, "NotOk")


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    try:
        value = json.loads(exc.read())["parameters"]["retry_after"]
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        return None
    return float(value) if isinstance(value, int | float) else None
