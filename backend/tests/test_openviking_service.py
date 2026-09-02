"""OpenVikingService request shape.

Scope: unit. The client is built for real; only settings are patched. Nothing
here proves the remote accepts the headers — that was measured live on
2026-09-02 (400 without, 200 with) and is recorded on the field itself.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services.openviking_service import OpenVikingService


def _service(url: str = "http://ov.test", key: str = "k") -> OpenVikingService:
    with patch("app.services.openviking_service.get_settings") as settings:
        settings.return_value.openviking_url = url
        settings.return_value.openviking_api_key = key
        return OpenVikingService()


def test_every_request_carries_the_tenant_headers_beside_the_bearer():
    # Root without a tenant is rejected by every data endpoint with 400; the
    # bearer alone is the shape that lost a day of agent insights.
    headers = _service(key="root-key")._headers

    assert headers["Authorization"] == "Bearer root-key"
    assert headers["X-OpenViking-Account"] == "default"
    assert headers["X-OpenViking-User"] == "default"


def test_disabled_without_url_or_key():
    assert _service(url="", key="k").enabled is False
    assert _service(url="http://ov.test", key="").enabled is False
    assert _service().enabled is True
