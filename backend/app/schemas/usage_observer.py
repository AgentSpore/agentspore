"""Closed, transient metadata supplied only by the scoped Otkrytka producer."""

from typing import Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, IPvAnyAddress, SecretStr, field_validator

from app.schemas.usage import GatewayVisit


class UsageObserverTarget(BaseModel):
    """Server-owned scope; the key is never part of public metadata or repr."""

    model_config = ConfigDict(frozen=True)
    project_id: UUID
    key: SecretStr = Field(repr=False)


class UsageObservation(BaseModel):
    """No path, body, contact, arbitrary project or asserted user identity is accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    request_id: UUID4 = Field(alias="x-usage-request-id", repr=False)
    visitor_id: str = Field(default="", alias="x-usage-visitor-id", max_length=80, repr=False)
    user_agent: str = Field(alias="x-usage-user-agent", max_length=4096, repr=False)
    accept: str = Field(alias="x-usage-accept", max_length=4096, repr=False)
    method: Literal["GET"] = Field(alias="x-usage-method")
    status: int = Field(alias="x-usage-status", ge=200, lt=300)
    content_type: str = Field(alias="x-usage-content-type", max_length=128)
    path_class: Literal["document", "probe"] = Field(alias="x-usage-path-class")
    client_ip: IPvAnyAddress = Field(alias="x-usage-client-ip", repr=False)

    @field_validator("content_type")
    @classmethod
    def require_html(cls, value: str) -> str:
        """Response headers describe HTML eligibility, never application content."""
        if value.split(";", 1)[0].strip().lower() != "text/html":
            raise ValueError("expected_html")
        return value

    def visit(self, visitor: UUID, user_id: UUID | None, new_cookie: bool) -> GatewayVisit:
        """Map the closed path class onto existing visit exclusions without a raw path."""
        return GatewayVisit(
            visitor_id=visitor,
            verified_user_id=user_id,
            user_agent=self.user_agent,
            accept=self.accept,
            method=self.method,
            path="/" if self.path_class == "document" else "/health",
            new_cookie=new_cookie,
        )
