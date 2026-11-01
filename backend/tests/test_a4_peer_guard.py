"""A4 tenant peer guard and serve kill switch: pure ASGI, no database."""

import ipaddress

import pytest
from starlette.datastructures import State
from starlette.responses import PlainTextResponse

from app.core.config import Settings
from app.main import app
from app.middleware.a4_peer_guard import A4PeerGuardMiddleware
from app.middleware.service_gateway import ServiceGatewayMiddleware
from app.schemas.usage import ServiceRoute

POOL = ipaddress.ip_network("10.214.0.0/16")


async def inner_app(scope, receive, send):
    if scope["type"] == "websocket":
        await send({"type": "websocket.accept"})
        return
    await PlainTextResponse("reached")(scope, receive, send)


def make_scope(kind, client, path="/", headers=()):
    scope = {
        "type": kind,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "method": "GET",
        "scheme": "ws" if kind == "websocket" else "http",
        "headers": [(k.encode(), v.encode()) for k, v in headers],
        "client": client,
        "server": ("testserver", 80),
    }
    if kind == "http":
        scope["http_version"] = "1.1"
    return scope


async def run(middleware, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


def status_of(sent):
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.fixture
def guard():
    return A4PeerGuardMiddleware(inner_app, pool=POOL)


@pytest.mark.parametrize(
    "path", ["/", "/health", "/metrics", "/api/v1/agents", "/_agentspore/events"]
)
async def test_tenant_peer_ip_blocked_from_api(guard, path):
    sent = await run(guard, make_scope("http", ("10.214.3.9", 4000), path))
    assert status_of(sent) == 403


async def test_tenant_peer_ip_blocked_on_gateway_host(guard):
    scope = make_scope("http", ("10.214.3.9", 4000), "/", [("host", "svc.agentspore.com")])
    assert status_of(await run(guard, scope)) == 403


async def test_tenant_peer_ip_blocked_on_websocket_before_accept(guard):
    sent = await run(guard, make_scope("websocket", ("10.214.3.9", 4000), "/ws"))
    assert [m["type"] for m in sent] == ["websocket.close"]
    assert sent[0]["code"] == 1008


async def test_public_websocket_reaches_app(guard):
    sent = await run(guard, make_scope("websocket", ("203.0.113.7", 4000), "/ws"))
    assert sent[0]["type"] == "websocket.accept"


async def test_peer_guard_ignores_forwarded(guard):
    spoof = [
        ("x-forwarded-for", "203.0.113.7"),
        ("forwarded", "for=203.0.113.7"),
        ("x-real-ip", "203.0.113.7"),
    ]
    sent = await run(guard, make_scope("http", ("10.214.3.9", 1), headers=spoof))
    assert status_of(sent) == 403
    framing = [("x-forwarded-for", "10.214.3.9"), ("x-real-ip", "10.214.3.9")]
    sent = await run(guard, make_scope("http", ("203.0.113.7", 1), headers=framing))
    assert status_of(sent) == 200


@pytest.mark.parametrize(
    ("peer", "blocked"),
    [
        ("10.213.255.255", False),
        ("10.214.0.0", True),
        ("10.214.255.255", True),
        ("10.215.0.0", False),
        ("::ffff:10.214.0.5", True),
        ("::ffff:10.215.0.5", False),
        ("2001:db8::1", False),
        ("::1", False),
    ],
)
async def test_pool_boundaries_and_ipv6_peer(guard, peer, blocked):
    sent = await run(guard, make_scope("http", (peer, 1)))
    assert status_of(sent) == (403 if blocked else 200)


@pytest.mark.parametrize("client", [None, ("", 0), ("not-an-ip", 1), ("unix", None)])
async def test_unparseable_client_passes(guard, client):
    assert status_of(await run(guard, make_scope("http", client))) == 200


async def test_guard_is_outermost_in_real_stack():
    assert app.user_middleware[0].cls is A4PeerGuardMiddleware


@pytest.fixture
def gateway(monkeypatch):
    """Gateway whose registry says the handle is an A4 service unless a test flips it."""
    reached = []
    state = {"platform_service": False}

    async def fake_resolve(self, handle):
        return ServiceRoute(
            project_id="00000000-0000-0000-0000-000000000001",
            handle=handle,
            active_revision="00000000-0000-0000-0000-000000000002",
        )

    async def fake_platform_service(self, route):
        return state["platform_service"]

    async def fake_forward(request, body, route, factory):
        reached.append(route.handle)
        return PlainTextResponse("upstream")

    async def fake_record(self, *args):
        return None

    monkeypatch.setattr(ServiceGatewayMiddleware, "_resolve", fake_resolve)
    monkeypatch.setattr(ServiceGatewayMiddleware, "_is_platform_service", fake_platform_service)
    monkeypatch.setattr(ServiceGatewayMiddleware, "_record", fake_record)
    monkeypatch.setattr("app.middleware.service_gateway.ServiceGateway.forward", fake_forward)
    return state, reached


def gateway_mw(serve_enabled):
    resources = State()
    resources.service_gateway_client_factory = None
    return ServiceGatewayMiddleware(
        inner_app, resources=resources, domains=["agentspore.com"], serve_enabled=serve_enabled
    )


async def test_serve_disabled_503_internal_untouched(gateway):
    state, reached = gateway
    scope = make_scope("http", ("203.0.113.7", 1), "/", [("host", "svc.agentspore.com")])
    sent = await run(gateway_mw(False), scope)
    assert status_of(sent) == 503
    assert b"serve_disabled" in b"".join(m.get("body", b"") for m in sent)
    assert reached == []

    state["platform_service"] = True
    assert status_of(await run(gateway_mw(False), scope)) == 200
    assert reached == ["svc"]


async def test_serve_enabled_forwards_a4(gateway):
    _, reached = gateway
    scope = make_scope("http", ("203.0.113.7", 1), "/", [("host", "svc.agentspore.com")])
    assert status_of(await run(gateway_mw(True), scope)) == 200
    assert reached == ["svc"]


async def test_serve_disabled_does_not_touch_platform_hosts(gateway):
    scope = make_scope("http", ("203.0.113.7", 1), "/", [("host", "agentspore.com")])
    assert status_of(await run(gateway_mw(False), scope)) == 200


def test_tenant_pool_setting_validated():
    assert Settings(a4_tenant_pool="10.214.0.0/16").a4_tenant_pool == POOL
    with pytest.raises(ValueError):
        Settings(a4_tenant_pool="10.214.0.5/16")
    with pytest.raises(ValueError):
        Settings(a4_tenant_pool="not-a-net")
    assert Settings().a4_serve_enabled is True
