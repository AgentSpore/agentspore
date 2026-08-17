"""Where the agent WS endpoint reads its credential from.

Backward compatibility is the point: agents deployed in the field authenticate
with `?api_key=`, and cutting them off to fix a logging bug would trade a leak
for an outage. So the query parameter still authenticates — it is merely
scrubbed out of the logs and reported per agent so the stragglers can be found.

No real key appears here; every value is fabricated.
"""

from __future__ import annotations

from app.api.v1.agents_ws import _KEY_SUBPROTOCOL_PREFIX, extract_ws_key

FAKE_KEY = "af_test_placeholder_0123456789abcdef"


class _FakeWS:
    """Minimal stand-in: extract_ws_key only ever reads headers."""

    def __init__(self, **headers: str) -> None:
        # Starlette lowercases header names; mirror that.
        self.headers = {k.replace("_", "-").lower(): v for k, v in headers.items()}


class TestLogSafeTransportsAreAccepted:
    def test_x_api_key_header(self) -> None:
        key, transport = extract_ws_key(_FakeWS(x_api_key=FAKE_KEY), None)
        assert (key, transport) == (FAKE_KEY, "header")

    def test_authorization_bearer(self) -> None:
        ws = _FakeWS(authorization=f"Bearer {FAKE_KEY}")
        assert extract_ws_key(ws, None) == (FAKE_KEY, "bearer")

    def test_bearer_scheme_is_case_insensitive(self) -> None:
        ws = _FakeWS(authorization=f"bearer {FAKE_KEY}")
        assert extract_ws_key(ws, None) == (FAKE_KEY, "bearer")

    def test_subprotocol(self) -> None:
        ws = _FakeWS(sec_websocket_protocol=f"{_KEY_SUBPROTOCOL_PREFIX}{FAKE_KEY}")
        assert extract_ws_key(ws, None) == (FAKE_KEY, "subprotocol")

    def test_subprotocol_among_several_offered(self) -> None:
        """Browsers send a comma-separated list; ours may not be first."""
        ws = _FakeWS(
            sec_websocket_protocol=f"json, {_KEY_SUBPROTOCOL_PREFIX}{FAKE_KEY}, other"
        )
        assert extract_ws_key(ws, None) == (FAKE_KEY, "subprotocol")


class TestBackwardCompatibility:
    def test_query_parameter_still_authenticates(self) -> None:
        """Field agents must not be cut off.

        MUTATION: stop reading the query parameter and this goes red — which is
        exactly the regression that would take every deployed agent offline.
        """
        key, transport = extract_ws_key(_FakeWS(), FAKE_KEY)
        assert key == FAKE_KEY
        assert transport == "query", "the deprecated path must be reported as such"

    def test_a_safe_transport_wins_over_the_query_parameter(self) -> None:
        """An updated agent that still appends the parameter must not be
        reported as deprecated, or the warning can never reach zero.
        """
        ws = _FakeWS(x_api_key=FAKE_KEY)
        assert extract_ws_key(ws, "af_stale_query_value") == (FAKE_KEY, "header")

    def test_no_credential_anywhere(self) -> None:
        assert extract_ws_key(_FakeWS(), None) == (None, "none")

    def test_unrelated_subprotocols_are_not_mistaken_for_a_key(self) -> None:
        ws = _FakeWS(sec_websocket_protocol="json, graphql-ws")
        assert extract_ws_key(ws, None) == (None, "none")
