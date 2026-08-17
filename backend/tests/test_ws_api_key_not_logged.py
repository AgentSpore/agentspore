"""An agent's API key must never reach a log sink.

Agents connect to ``/api/v1/agents/ws?api_key=af_...``. Uvicorn logs the
request line WITH the query string, so every successful agent connection
wrote that agent's live credential verbatim into the container log — and the
rotating file sink, and anything shipping either onward. Observed in
production 2026-08-17.

The leak is NOT in our code: uvicorn's websocket implementation formats the
line itself (``websockets_impl`` logs ``'%s - "WebSocket %s" [accepted]'``
with ``get_path_with_query_string(scope)``) on the ``uvicorn.error`` logger —
not ``uvicorn.access``, which is why scrubbing only the access logger would
have missed it entirely. Three sites emit it (accepted / 403 / status), and
the HTTP path has its own on ``uvicorn.access``.

What these tests falsify is the OBSERVABLE OUTPUT: a record carrying a
credential is emitted through the real handler into a real sink, and the sink
contents are searched for the secret. Asserting that a new auth path works
would prove nothing about whether the old one stopped leaking.

No real key appears here; ``af_test_placeholder`` and friends are fabricated.
"""

from __future__ import annotations

import logging

import pytest
from loguru import logger

from app.core.logging import _InterceptHandler, redact_secrets

# A fabricated key shaped like the real thing (prefix + long opaque tail).
FAKE_KEY = "af_test_placeholder_0123456789abcdef0123456789abcdef"


@pytest.fixture
def sink() -> list[str]:
    """Capture what actually lands in a loguru sink, via the real handler."""
    written: list[str] = []
    logger.remove()
    sink_id = logger.add(written.append, format="{message}", level="DEBUG")
    yield written
    logger.remove(sink_id)


def _emit_through_stdlib(msg: str, *args: object, name: str = "uvicorn.error") -> None:
    """Push a record through _InterceptHandler exactly as uvicorn would.

    Uses the same lazy %-style formatting uvicorn uses, so the credential is
    only present after `record.getMessage()` interpolates the args — a filter
    that inspected `record.msg` alone would see a bare '%s' and pass it.
    """
    handler = _InterceptHandler()
    record = logging.LogRecord(
        name=name, level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=None,
    )
    handler.emit(record)


class TestUvicornWebsocketLineIsScrubbed:
    """The exact production leak, reproduced through the real code path."""

    def test_accepted_websocket_line_does_not_carry_the_key(self, sink: list[str]) -> None:
        """MUTATION: drop the redact_secrets() call in _InterceptHandler.emit
        and this goes red — the key lands in the sink verbatim.
        """
        _emit_through_stdlib(
            '%s - "WebSocket %s" [accepted]',
            "10.0.0.7:51234",
            f"/api/v1/agents/ws?api_key={FAKE_KEY}",
        )

        assert sink, "nothing reached the sink — the test proves nothing"
        blob = "\n".join(sink)
        assert FAKE_KEY not in blob, "the agent API key reached a log sink"
        # The line must remain useful for debugging: route still identifiable.
        assert "/api/v1/agents/ws" in blob
        assert "10.0.0.7:51234" in blob

    @pytest.mark.parametrize(
        "template",
        [
            '%s - "WebSocket %s" [accepted]',
            '%s - "WebSocket %s" 403',
            '%s - "WebSocket %s" 500',
        ],
        ids=["accepted", "denied", "status"],
    )
    def test_every_uvicorn_websocket_log_site_is_scrubbed(
        self, sink: list[str], template: str
    ) -> None:
        """uvicorn emits the query string from three distinct sites; a fix that
        covered only the accepted one would still leak on the others.
        """
        _emit_through_stdlib(
            template, "10.0.0.7:51234", f"/api/v1/agents/ws?api_key={FAKE_KEY}"
        )
        assert FAKE_KEY not in "\n".join(sink)

    def test_http_access_logger_is_scrubbed_too(self, sink: list[str]) -> None:
        """The HTTP path logs the query string on `uvicorn.access`. The SSE
        fallback takes credentials by header today, but a key in any HTTP query
        string must not depend on that staying true.
        """
        _emit_through_stdlib(
            '%s - "%s %s HTTP/1.1" %d',
            "10.0.0.7:51234",
            "GET",
            f"/api/v1/agents/events?api_key={FAKE_KEY}",
            200,
            name="uvicorn.access",
        )
        assert FAKE_KEY not in "\n".join(sink)

    def test_scrubbing_is_not_limited_to_the_agents_route(self, sink: list[str]) -> None:
        """Redaction keys off the PARAMETER, not the path: a credential in a
        query string is a leak wherever it appears.
        """
        _emit_through_stdlib(
            '%s - "WebSocket %s" [accepted]',
            "10.0.0.7:51234",
            f"/api/v1/users/ws?token={FAKE_KEY}",
        )
        assert FAKE_KEY not in "\n".join(sink)


class TestRedactSecrets:
    """Unit-level behaviour of the scrubber itself."""

    @pytest.mark.parametrize(
        "param", ["api_key", "apikey", "token", "access_token", "key", "secret", "password"]
    )
    def test_known_credential_parameters_are_masked(self, param: str) -> None:
        assert FAKE_KEY not in redact_secrets(f"/ws?{param}={FAKE_KEY}")

    def test_parameter_matching_is_case_insensitive(self) -> None:
        assert FAKE_KEY not in redact_secrets(f"/ws?API_KEY={FAKE_KEY}")

    def test_only_the_value_is_removed(self) -> None:
        """A scrubbed line must still say WHICH parameter was scrubbed, or the
        log stops being diagnostic.
        """
        out = redact_secrets(f"/api/v1/agents/ws?api_key={FAKE_KEY}")
        assert "api_key" in out
        assert "/api/v1/agents/ws" in out
        assert FAKE_KEY not in out

    def test_other_parameters_survive(self) -> None:
        out = redact_secrets(f"/ws?api_key={FAKE_KEY}&agent=scout&v=2")
        assert "agent=scout" in out
        assert "v=2" in out
        assert FAKE_KEY not in out

    def test_credential_in_the_middle_of_a_query_is_masked(self) -> None:
        out = redact_secrets(f"/ws?v=2&api_key={FAKE_KEY}&x=1")
        assert FAKE_KEY not in out
        assert "x=1" in out

    def test_a_line_with_no_credential_is_returned_unchanged(self) -> None:
        line = '10.0.0.7 - "GET /api/v1/agents/leaderboard HTTP/1.1" 200'
        assert redact_secrets(line) == line

    def test_bare_af_token_anywhere_is_masked(self) -> None:
        """Defence in depth: an agent key is recognisable by its own prefix, so
        it is masked even when it is not a query parameter at all (an exception
        message, a repr of a connect URL, a hand-rolled log line).
        """
        assert FAKE_KEY not in redact_secrets(f"connect failed for {FAKE_KEY}")
