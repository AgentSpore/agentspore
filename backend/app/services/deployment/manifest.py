"""`agentspore.toml` project manifest (spec Q11): strict, closed set of keys."""

import re
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from app.services.deployment.errors import DeployRejectedError

MANIFEST_NAME = "agentspore.toml"
MAX_MANIFEST_BYTES = 64 * 1024
RESERVED_ENV = frozenset({"PORT", "PUBLIC_URL"})
_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_HOST = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")


def _env_name(value: str) -> str:
    if _NAME.fullmatch(value) is None:
        raise ValueError("must match [A-Z][A-Z0-9_]{0,63}")
    return value


def _env_map(value: dict[str, str]) -> dict[str, str]:
    for name in value:
        _env_name(name)
        if name in RESERVED_ENV:
            raise ValueError(f"{name} is set by the platform")
    return value


def _unique(value: list[str]) -> list[str]:
    if len(set(value)) != len(value):
        raise ValueError("duplicates")
    return value


def _host(value: str) -> str:
    if _HOST.fullmatch(value) is None:
        raise ValueError("must be a lower-case hostname")
    return value


class Manifest(BaseModel):
    """Absent file means every default. Secrets are names only; values use the secrets API."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    port: int = Field(default=8080, ge=1024, le=65535)
    health: str = Field(default="/health", pattern=r"^/[\x21-\x7e]{0,255}$")
    build: Literal["dockerfile", "auto"] = "dockerfile"
    env: Annotated[dict[str, Annotated[str, Field(max_length=1024)]], AfterValidator(_env_map)] = (
        Field(default_factory=dict, max_length=50)
    )
    secrets: Annotated[list[Annotated[str, AfterValidator(_env_name)]], AfterValidator(_unique)] = (
        Field(default_factory=list, max_length=20)
    )
    storage: bool = False
    egress: list[Annotated[str, AfterValidator(_host)]] = Field(default_factory=list, max_length=20)


def load_manifest(root: Path) -> Manifest:
    """Parse `root/agentspore.toml`; `manifest_invalid` carries only key paths, never values."""
    path = root / MANIFEST_NAME
    if not path.is_file():
        return Manifest()
    raw = path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise DeployRejectedError(
            "manifest_invalid", f"{MANIFEST_NAME} over {MAX_MANIFEST_BYTES} bytes"
        )
    try:
        data = tomllib.loads(raw.decode())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise DeployRejectedError("manifest_invalid", "not valid TOML") from exc
    try:
        return Manifest.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['type']}" for e in exc.errors()
        )
        raise DeployRejectedError("manifest_invalid", problems) from exc
