"""Closed, transient metadata supplied only by the scoped Otkrytka producer."""

from typing import Literal, Self
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, IPvAnyAddress, SecretStr, model_validator

from app.schemas.usage import GatewayVisit

UsageServiceName = Literal["otkrytka", "podmemory", "pereklichka"]


class UsageObserverTarget(BaseModel):
    """Server-owned scope; the key is never part of public metadata or repr."""

    model_config = ConfigDict(frozen=True)
    service: UsageServiceName = "otkrytka"
    project_id: UUID
    key: SecretStr = Field(repr=False)


class UsageObservation(BaseModel):
    """No path, body, contact, arbitrary project or asserted user identity is accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    request_id: UUID4 = Field(alias="x-usage-request-id", repr=False)
    visitor_id: str = Field(default="", alias="x-usage-visitor-id", max_length=80, repr=False)
    user_agent: str = Field(alias="x-usage-user-agent", max_length=4096, repr=False)
    accept: str = Field(alias="x-usage-accept", max_length=4096, repr=False)
    kind: Literal["visit", "board_created"] = Field(alias="x-usage-kind")
    method: Literal["GET", "POST"] = Field(alias="x-usage-method")
    status: int = Field(alias="x-usage-status", ge=200, lt=300)
    content_type: str = Field(alias="x-usage-content-type", max_length=128)
    path_class: Literal["document", "probe", "board_creation"] = Field(alias="x-usage-path-class")
    client_ip: IPvAnyAddress = Field(alias="x-usage-client-ip", repr=False)

    @model_validator(mode="after")
    def require_matching_response(self) -> Self:
        """Only the closed, source-verified response combinations are eligible."""
        mime = self.content_type.split(";", 1)[0].strip().lower()
        visit = (
            self.kind == "visit"
            and self.method == "GET"
            and mime == "text/html"
            and self.path_class in {"document", "probe"}
        )
        action = (
            self.kind == "board_created"
            and self.method == "POST"
            and self.status == 200
            and mime == "application/json"
            and self.path_class == "board_creation"
        )
        if not (visit or action):
            raise ValueError("invalid_observation_response")
        return self

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
