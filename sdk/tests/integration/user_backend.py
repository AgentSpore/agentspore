"""Run the genuine backend with private email capture and no external delivery."""

import argparse
import importlib
import json
import socket
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from loguru import logger

from user_diagnostics import safe_exception

_ASYNC_SEND = httpx.AsyncClient.send
_SYNC_SEND = httpx.Client.send
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _require_local(request: httpx.Request) -> None:
    if request.url.host not in _LOOPBACK:
        raise httpx.ConnectError("External delivery disabled by local proof", request=request)


async def _async_send(self: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
    _require_local(request)
    return await _ASYNC_SEND(self, request, **kwargs)


def _sync_send(self: httpx.Client, request: httpx.Request, **kwargs: Any) -> httpx.Response:
    _require_local(request)
    return _SYNC_SEND(self, request, **kwargs)


class CapturedEmail:
    """Deliver only this disposable verification token to the inherited private socket."""

    def __init__(self, channel: socket.socket):
        self.channel = channel

    async def send_verification_email(self, to: str, token: str, base_url: str | None = None) -> bool:
        """Acknowledge capture so the backend never falls back to a token-bearing log."""
        payload = json.dumps({"to": to, "token": token}).encode() + b"\n"
        self.channel.sendall(payload)
        return True


class DiagnosticBoundary:
    """Rethrow genuine ASGI failures after private, value-free diagnostic capture."""

    def __init__(self, application: Any, channel: socket.socket):
        self.application = application
        self.channel = channel

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Preserve ASGI responses and exceptions; never serialize messages or locals."""
        try:
            await self.application(scope, receive, send)
        except Exception as error:
            data = {"kind": "exception", **safe_exception(error)}
            try:
                self.channel.sendall(json.dumps(data).encode() + b"\n")
            except OSError:
                pass  # Diagnostic delivery is best-effort; the original failure still propagates.
            raise


def _quiet_logging() -> None:
    logger.remove()


def create_application(channel: socket.socket) -> Any:
    """Apply only delivery, scheduler and logging boundaries before genuine startup."""
    if Path(".env").exists():
        raise RuntimeError("Unexpected environment file in disposable runtime")
    logger.remove()
    logging_module = importlib.import_module("app.core.logging")
    setattr(logging_module, "setup_logging", _quiet_logging)
    httpx.AsyncClient.send = _async_send
    httpx.Client.send = _sync_send
    main_module = importlib.import_module("app.main")
    email_module = importlib.import_module("app.services.email_service")
    setattr(main_module, "spawn_background_tasks", lambda: [])
    capture = CapturedEmail(channel)
    main_module.app.dependency_overrides[email_module.get_email_service] = lambda: capture
    return DiagnosticBoundary(main_module.app, channel)


def main() -> None:
    """Start only when called explicitly by the owned-stand coordinator."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--email-fd", type=int, required=True)
    args = parser.parse_args()
    with socket.socket(fileno=args.email_fd) as channel:
        application = create_application(channel)
        uvicorn.run(application, host="127.0.0.1", port=args.port, access_log=False, log_config=None)


if __name__ == "__main__":
    main()
