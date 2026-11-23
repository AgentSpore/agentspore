"""Suite-wide guard: no SDK test may send a request to a real host."""

import httpx
import pytest


def _refuse(request: httpx.Request):
    raise RuntimeError(
        f"Test tried a real network call to {request.url.host!r} "
        f"({request.method} {request.url}). Use httpx.MockTransport."
    )


async def _refuse_async(request: httpx.Request):
    _refuse(request)


# INVARIANT(sdk-tests-offline): removing this lets a test register real agents on prod
@pytest.fixture(autouse=True)
def _offline_guard(monkeypatch):
    """Make the real httpx transports fail loudly.

    Patches the transport layer, so it catches every way of building a client
    (`httpx.Client`, `from httpx import Client`, AsyncClient, module-level
    clients). `httpx.MockTransport` is a different class and keeps working.
    """
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: _refuse(request)
    )
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport,
        "handle_async_request",
        lambda self, request: _refuse_async(request),
    )
