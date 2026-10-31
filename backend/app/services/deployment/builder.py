"""Build seam: P3 supplies the rootless BuildKit implementation; tests use `FakeBuilder`."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.services.deployment.errors import DeployRejectedError


@dataclass(frozen=True)
class BuildRequest:
    context_dir: Path
    tag: str
    dockerfile: str = "Dockerfile"


@dataclass(frozen=True)
class BuildResult:
    image_ref: str
    image_bytes: int


@runtime_checkable
class Builder(Protocol):
    async def build(self, request: BuildRequest) -> BuildResult:
        """Build the context; refuse with `DeployRejectedError` (e.g. `image_too_large`)."""
        ...


@dataclass
class FakeBuilder:
    """Records requests; returns `result` or raises `error`."""

    result: BuildResult | None = None
    error: DeployRejectedError | None = None
    requests: list[BuildRequest] = field(default_factory=list)

    async def build(self, request: BuildRequest) -> BuildResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result or BuildResult(image_ref=request.tag, image_bytes=0)
