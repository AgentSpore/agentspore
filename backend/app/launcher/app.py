"""HTTP surface of the launcher. Only the routes below exist; no docs, no openapi."""

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.launcher.docker import DockerError
from app.launcher.schemas import (
    MAX_BODY,
    NS_RE,
    GcRequest,
    LauncherError,
    RuntimeRequest,
    parse_body,
)
from app.launcher.service import Launcher


async def _read(request: Request) -> bytes:
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise LauncherError(422, "body_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _no_body(request: Request) -> None:
    if await _read(request):
        raise LauncherError(422, "invalid_request")


def _ns(ns: str) -> str:
    if not NS_RE.fullmatch(ns):
        raise LauncherError(403, "ns_invalid")
    return ns


def _runtime_router(launcher: Launcher) -> APIRouter:
    router = APIRouter(prefix="/v1/runtimes")

    @router.put("/{ns}")
    async def put_runtime(ns: str, request: Request) -> dict[str, str]:
        req = parse_body(await _read(request), RuntimeRequest)
        if _ns(ns) != req.namespace:
            raise LauncherError(403, "ns_mismatch")
        return {"container_id": await launcher.put(ns, req)}

    @router.post("/{ns}/start")
    async def start_runtime(ns: str, request: Request) -> dict[str, bool]:
        await _no_body(request)
        await launcher.start(_ns(ns))
        return {"ok": True}

    @router.post("/{ns}/stop")
    async def stop_runtime(ns: str, request: Request) -> dict[str, bool]:
        await _no_body(request)
        await launcher.stop(_ns(ns))
        return {"ok": True}

    @router.delete("/{ns}")
    async def delete_runtime(ns: str, request: Request) -> dict[str, bool]:
        await _no_body(request)
        await launcher.delete(_ns(ns))
        return {"ok": True}

    @router.get("")
    async def list_runtimes() -> dict[str, list[dict[str, str]]]:
        return {
            "runtimes": await launcher.list_runtimes(),
            "networks": await launcher.list_networks(),
        }

    return router


def build_app(launcher: Launcher) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(LauncherError)
    async def _launcher_error(_: Request, exc: LauncherError) -> JSONResponse:
        return JSONResponse({"reason": exc.reason}, status_code=exc.status)

    @app.exception_handler(DockerError)
    async def _docker_error(_: Request, exc: DockerError) -> JSONResponse:
        logger.warning("docker error status={} text={}", exc.status, exc.text)
        return JSONResponse(
            {"reason": "docker_error", "docker_status": exc.status}, status_code=502
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/images/gc")
    async def gc_images(request: Request) -> dict[str, list[str]]:
        body = parse_body(await _read(request), GcRequest)
        return {"removed": await launcher.gc_images(body.keep)}

    app.include_router(_runtime_router(launcher))
    return app
