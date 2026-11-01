"""Strict launcher request schemas. No Docker field is expressible in any of them."""

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

MAX_BODY = 16 * 1024
MAX_ENV_KEYS = 64
MAX_ENV_BYTES = 32 * 1024
NS_RE = re.compile(r"a4-([0-9a-f]{32})-([1-9][0-9]*)")
IMAGE_RE = re.compile(r"127\.0\.0\.1:5000/(a4-[0-9a-f]{32}-[1-9][0-9]*)@sha256:[0-9a-f]{64}")
ENV_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
WORKER_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
URL_RE = re.compile(r"https://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?/?")
RESERVED_ENV = frozenset({"PORT", "PUBLIC_URL", "HOSTNAME", "HTTP_PROXY", "HTTPS_PROXY"})

M = TypeVar("M", bound=BaseModel)


class LauncherError(Exception):
    def __init__(self, status: int, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.status, self.reason, self.detail = status, reason, detail


class RuntimeRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    deployment_id: StrictStr
    generation: StrictInt = Field(ge=1)
    worker_id: StrictStr
    image: StrictStr = Field(max_length=256)
    env: dict[StrictStr, StrictStr]
    public_url: StrictStr

    @field_validator("deployment_id")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", v):
            raise ValueError("deployment_id")
        return v

    @field_validator("worker_id")
    @classmethod
    def _worker(cls, v: str) -> str:
        if not WORKER_RE.fullmatch(v):
            raise ValueError("worker_id")
        return v

    @field_validator("public_url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not URL_RE.fullmatch(v):
            raise ValueError("public_url")
        return v

    @field_validator("env")
    @classmethod
    def _env(cls, v: dict[str, str]) -> dict[str, str]:
        size = sum(len(k) + len(x) for k, x in v.items())
        if len(v) > MAX_ENV_KEYS or size > MAX_ENV_BYTES:
            raise ValueError("env limits")
        for name, value in v.items():
            if not ENV_NAME_RE.fullmatch(name) or name in RESERVED_ENV or "\x00" in value:
                raise ValueError("env name")
        return v

    @property
    def namespace(self) -> str:
        return f"a4-{self.deployment_id}-{self.generation}"


class GcRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    keep: list[StrictStr] = Field(max_length=1000)

    @field_validator("keep")
    @classmethod
    def _refs(cls, v: list[str]) -> list[str]:
        if not all(IMAGE_RE.fullmatch(r) for r in v):
            raise ValueError("keep")
        return v


def _no_dup_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _no_constant(name: str) -> Any:
    raise ValueError(name)


def parse_body(raw: bytes, model: type[M]) -> M:
    """Parse untrusted JSON: size cap, no duplicate keys, no NaN, case-exact strict fields."""
    if len(raw) > MAX_BODY:
        raise LauncherError(422, "body_too_large")
    try:
        data = json.loads(raw, object_pairs_hook=_no_dup_keys, parse_constant=_no_constant)
        return model.model_validate(data)
    except (ValueError, RecursionError) as exc:
        raise LauncherError(422, "invalid_request") from exc
