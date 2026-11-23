"""Session-wide guard: no SDK test may send a request to a real host."""

import httpx
import pytest

_PATCH = pytest.StashKey[pytest.MonkeyPatch]()


def _refuse(request: httpx.Request):
    raise RuntimeError(
        f"Test tried a real network call to {request.url.host!r} "
        f"({request.method} {request.url}). Use httpx.MockTransport."
    )


async def _refuse_async(request: httpx.Request):
    _refuse(request)


# INVARIANT(sdk-tests-offline): removing this lets a test register real agents on prod
def pytest_configure(config):
    """Make the real httpx transports fail loudly for the whole session.

    Installed before collection, so import-time code and module- or
    session-scoped fixtures are covered too. Patching the transport layer
    catches every way of building a client (`httpx.Client`, `from httpx import
    Client`, AsyncClient). `httpx.MockTransport` is a different class and keeps
    working. A raw socket opened outside httpx is not covered.
    """
    patch = pytest.MonkeyPatch()
    patch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: _refuse(request)
    )
    patch.setattr(
        httpx.AsyncHTTPTransport,
        "handle_async_request",
        lambda self, request: _refuse_async(request),
    )
    config.stash[_PATCH] = patch


def pytest_unconfigure(config):
    patch = config.stash.get(_PATCH, None)
    if patch is not None:
        patch.undo()
