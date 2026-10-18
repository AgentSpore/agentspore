"""OpenVikingService authenticates with a ROOT key, so every request needs
the tenant headers too or tenant-scoped APIs answer 400 while /health stays
green (INVARIANT(openviking-tenant) in the service)."""

from app.core.config import Settings
from app.services.openviking_service import OpenVikingService


def test_headers_carry_both_tenant_headers_alongside_the_bearer_token(monkeypatch) -> None:
    settings = Settings(openviking_url="https://ov.example.invalid", openviking_api_key="root-key")
    monkeypatch.setattr(
        "app.services.openviking_service.get_settings", lambda: settings
    )

    service = OpenVikingService()

    assert service._headers["Authorization"] == "Bearer root-key"
    assert service._headers["X-OpenViking-Account"] == "default"
    assert service._headers["X-OpenViking-User"] == "default"
