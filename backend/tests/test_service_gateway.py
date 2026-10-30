"""Real main-app ingress, PostgreSQL usage, Redis TTL, and outward HTTP transport tests."""

import asyncio
import hashlib
import hmac
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import event, text
from test_service_usage import BROWSER, TODAY
from test_service_usage import usage_clock as usage_clock
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world
from testcontainers.redis import RedisContainer

from app.core.config import get_settings
from app.core.security import create_access_token, create_refresh_token
from app.main import app
from app.middleware.service_gateway import ServiceGatewayMiddleware
from app.services.usage_service import UsageService

SERVICE_HTML = "<!doctype html><html><body><h1>A2 synthetic service</h1></body></html>"


class Chunks(httpx.AsyncByteStream):
    """A bounded outward HTTP response stream controlled by each test."""

    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


@pytest.fixture(scope="session")
def gateway_redis_container():
    with RedisContainer(
        "redis:7-alpine",
        mem_limit="64m",
        nano_cpus=100000000,
        labels={"a2.owner": "a2-gateway-20261016"},
    ) as container:
        yield container


@pytest.fixture
async def gateway_redis(gateway_redis_container):
    container = gateway_redis_container
    client = Redis(
        host=container.get_container_host_ip(),
        port=int(container.get_exposed_port(6379)),
        decode_responses=True,
    )
    try:
        await client.flushdb()
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def gateway_route(usage_world):
    project_id = usage_world.projects[0]
    handle, revision = "a2-" + uuid4().hex, uuid4()
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO service_hostnames (handle, original_project_id) "
                "VALUES (:handle, :project)"
            ),
            {"handle": handle, "project": project_id},
        )
        await db.execute(
            text(
                "INSERT INTO service_routes (project_id, handle, active_revision) "
                "VALUES (:project, :handle, :revision)"
            ),
            {"project": project_id, "handle": handle, "revision": revision},
        )
    return {"project_id": project_id, "handle": handle, "revision": revision}


@pytest.fixture(autouse=True)
async def gateway_boundaries(usage_world, gateway_route, gateway_redis, usage_clock, monkeypatch):
    control = {
        "requests": [],
        "status": 200,
        "chunks": [SERVICE_HTML.encode()],
        "headers": {"content-type": "text/html"},
        "delay": 0,
        "error": None,
    }

    async def upstream(request):
        control["requests"].append(request)
        if "if-none-match" in request.headers or "if-modified-since" in request.headers:
            return httpx.Response(304)
        if control["delay"]:
            await asyncio.sleep(control["delay"])
        if control["error"] is not None:
            raise control["error"]
        return httpx.Response(
            control["status"], headers=control["headers"], stream=Chunks(control["chunks"])
        )

    async def redis_factory():
        return gateway_redis

    def client_factory():
        return AsyncClient(transport=httpx.MockTransport(upstream), trust_env=False)

    monkeypatch.setattr(app.state, "service_gateway_db_factory", usage_world.maker)
    monkeypatch.setattr(app.state, "service_gateway_redis_factory", redis_factory)
    monkeypatch.setattr(app.state, "service_gateway_client_factory", client_factory)
    return control


@pytest.fixture
async def gateway_api(gateway_route, gateway_boundaries):
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.18", 4321)),
        base_url="https://" + gateway_route["handle"] + ".agentspore.com",
        headers={"user-agent": BROWSER, "accept": "text/html"},
    ) as client:
        yield client, gateway_boundaries


async def test_actual_main_routes_service_hostname_to_registered_revision(gateway_route):
    hostname = gateway_route["handle"] + ".agentspore.com"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://agentspore.com",
    ) as client:
        response = await client.get("/", headers={"Host": hostname})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == SERVICE_HTML


@pytest.mark.parametrize("path", ["/", "/%2F%2Fevil.invalid/x", "/api/v1/projects"])
async def test_service_target_uses_registry_uuid_even_for_encoded_paths(
    gateway_api, gateway_route, path
):
    client, control = gateway_api
    response = await client.get(path, params={"target": "http://evil.invalid/"})
    assert response.text == SERVICE_HTML
    target = control["requests"][0].url
    expected_host = "svc-" + gateway_route["revision"].hex
    assert (target.scheme, target.host, target.port) == ("http", expected_host, 8080)
    assert len(control["requests"]) == 1


@pytest.mark.parametrize("host", ["agentspore.com", "www.agentspore.com", "api.agentspore.com"])
async def test_platform_hosts_keep_platform_root(gateway_api, host):
    client, control = gateway_api
    response = await client.get("/", headers={"Host": host})
    assert response.status_code == 200
    assert response.json()["agent_registration"] == "/api/v1/agents/register"
    assert control["requests"] == []


@pytest.mark.parametrize("host", ["unknown.agentspore.com", "nested.unknown.agentspore.com"])
async def test_unknown_service_cannot_reach_platform_api(gateway_api, host):
    client, control = gateway_api
    response = await client.get("/api/v1/projects", headers={"Host": host})
    assert response.status_code == 404
    assert control["requests"] == []


async def test_service_websocket_is_closed_before_platform_dispatch(gateway_route):
    messages = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "websocket",
            "path": "/api/v1/ws",
            "query_string": b"",
            "headers": [(b"host", (gateway_route["handle"] + ".agentspore.com").encode())],
        },
        receive,
        send,
    )
    assert messages == [{"type": "websocket.close", "code": 1008}]


async def test_platform_credentials_never_reach_service_and_response_cookies_are_stripped(
    gateway_api,
):
    client, control = gateway_api
    control["headers"]["set-cookie"] = "platform=synthetic; Domain=.agentspore.com; Secure"
    response = await client.get(
        "/",
        headers={
            "authorization": "Bearer invalid",
            "cookie": "platform=synthetic",
            "x-api-key": "synthetic",
            "x-user-id": str(uuid4()),
            "x-forwarded-for": "192.0.2.1",
        },
    )
    headers = control["requests"][0].headers
    assert not {"authorization", "cookie", "x-api-key", "x-user-id", "x-forwarded-for"} & set(
        headers
    )
    assert headers["host"].startswith("svc-")
    (cookie,) = response.headers.get_list("set-cookie")
    assert cookie.startswith("__Host-visitor=")
    assert all(
        part in cookie.lower()
        for part in ["secure", "httponly", "samesite=lax", "path=/", "max-age=691200"]
    )
    assert "domain=" not in cookie.lower()


async def test_valid_cookie_deduplicates_and_malformed_cookie_is_replaced(gateway_api, usage_world):
    client, _ = gateway_api
    first = await client.get("/")
    visitor = first.cookies["__Host-visitor"]
    assert UUID(visitor).version == 4
    repeat = await client.get("/")
    assert "set-cookie" not in repeat.headers
    replaced = await client.get("/", headers={"cookie": "__Host-visitor=not-a-uuid"})
    assert UUID(replaced.cookies["__Host-visitor"]) != UUID(visitor)
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 2


@pytest.mark.parametrize(
    "identity,count",
    [
        ("owner", 0),
        ("stranger", 1),
        ("invalid", 1),
        ("refresh", 1),
        ("expired", 1),
        ("nonuuid", 1),
        ("spoof", 1),
    ],
)
async def test_only_real_current_access_jwt_owner_is_excluded(
    gateway_api, usage_world, identity, count, gateway_redis
):
    client, _ = gateway_api
    owner = str(usage_world.owner)
    tokens = {
        "owner": create_access_token(owner),
        "stranger": create_access_token(str(uuid4())),
        "invalid": "invalid",
        "refresh": create_refresh_token(owner),
        "expired": create_access_token(owner, timedelta(seconds=-5)),
        "nonuuid": create_access_token("not-a-uuid"),
        "spoof": "",
    }
    response = await client.get(
        "/", headers={"authorization": "Bearer " + tokens[identity], "x-user-id": owner}
    )
    assert response.status_code == 200
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == count
    assert await gateway_redis.dbsize() == count


@pytest.mark.parametrize(
    "traffic",
    [
        ("/", "Googlebot/2.1", 200, "text/html"),
        ("/health", BROWSER, 200, "text/html"),
        ("/api/data", BROWSER, 200, "text/html"),
        ("/assets/app.js", BROWSER, 200, "text/html"),
        ("/page.css", BROWSER, 200, "text/html"),
        ("/", BROWSER, 500, "text/html"),
        ("/", BROWSER, 200, "application/json"),
    ],
)
async def test_ineligible_service_traffic_never_writes_usage(
    gateway_api, usage_world, traffic, gateway_redis
):
    path, agent, status, content_type = traffic
    client, control = gateway_api
    control["status"], control["headers"]["content-type"] = status, content_type
    assert (await client.get(path, headers={"user-agent": agent})).status_code == status
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0
    assert await gateway_redis.dbsize() == 0


async def test_ip_is_only_a_domain_day_hmac_with_nonextending_redis_ttl(
    gateway_api, gateway_route, gateway_redis
):
    client, _ = gateway_api
    await client.get("/", headers={"x-forwarded-for": "192.0.2.44"})
    domain = gateway_route["handle"] + ".agentspore.com"
    message = (TODAY.isoformat() + "\0" + domain + "\0" + "203.0.113.18").encode()
    key = get_settings().secret_key.encode()
    expected = "service-ip:" + hmac.new(key, message, hashlib.sha256).hexdigest()
    assert await gateway_redis.keys("*") == [expected]
    assert 86390 <= await gateway_redis.ttl(expected) <= 86400
    assert await gateway_redis.get(expected) == "1"
    await gateway_redis.expire(expected, 43)
    await client.get("/", headers={"cookie": "__Host-visitor=" + str(uuid4())})
    assert 0 < await gateway_redis.ttl(expected) <= 43


async def test_redis_outage_does_not_break_successful_service(gateway_api, monkeypatch):
    client, _ = gateway_api
    unavailable = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.1)

    async def redis_factory():
        return unavailable

    monkeypatch.setattr(app.state, "service_gateway_redis_factory", redis_factory)
    try:
        response = await client.get("/")
        assert response.status_code == 200 and response.text == SERVICE_HTML
    finally:
        await unavailable.aclose()


@pytest.mark.parametrize("size,expected", [(256 * 1024, 200), (256 * 1024 + 1, 413)])
async def test_request_body_bound_is_enforced_before_upstream(gateway_api, size, expected):
    client, control = gateway_api
    response = await client.post("/", content=b"x" * size)
    assert response.status_code == expected
    assert len(control["requests"]) == int(expected == 200)


@pytest.mark.parametrize("chunks,expected", [(32, 200), (33, 502)])
async def test_response_stream_is_bounded_at_two_mib(gateway_api, chunks, expected):
    client, control = gateway_api
    control["chunks"], control["headers"] = (
        [b"x" * 65536] * chunks,
        {"content-type": "application/octet-stream"},
    )
    assert (await client.get("/")).status_code == expected


@pytest.mark.parametrize(
    "failure,expected",
    [("redirect", 502), ("timeout", 504), ("connect", 502), ("headers", 502), ("compression", 502)],
)
async def test_upstream_failures_never_redirect_or_leak_internal_errors(
    gateway_api, failure, expected
):
    client, control = gateway_api
    if failure == "redirect":
        control["status"], control["headers"]["location"] = 302, "http://evil.invalid/"
    elif failure == "headers":
        control["headers"]["x-large"] = "x" * 32769
    elif failure == "compression":
        control["headers"]["content-encoding"] = "gzip"
    else:
        control["error"] = (httpx.ReadTimeout if failure == "timeout" else httpx.ConnectError)(
            "Synthetic upstream failure"
        )
    response = await client.get("/")
    assert response.status_code == expected
    assert len(control["requests"]) == 1
    assert "svc-" not in response.text and "Synthetic" not in response.text


async def test_upstream_deadline_bounds_a_stalled_transport(gateway_api):
    client, control = gateway_api
    control["delay"] = 11
    assert (await client.get("/")).status_code == 504


async def test_request_headers_are_bounded_before_upstream(gateway_api):
    client, control = gateway_api
    assert (await client.get("/", headers={"x-large": "x" * 32769})).status_code == 431
    assert control["requests"] == []


async def test_slow_body_times_out_before_database_checkout(gateway_api, usage_world):
    client, control = gateway_api
    checkouts = []
    engine = usage_world.maker.kw["bind"].sync_engine

    def capture(*args):
        checkouts.append(1)

    async def stalled():
        await asyncio.sleep(11)
        yield b"x"

    event.listen(engine, "checkout", capture)
    try:
        response = await client.post("/", content=stalled())
    finally:
        event.remove(engine, "checkout", capture)
    assert response.status_code == 408
    assert checkouts == [] and control["requests"] == []


async def test_host_cookies_and_daily_ip_hmac_are_separate(
    gateway_api, usage_world, gateway_redis, monkeypatch
):
    client, _ = gateway_api
    first = await client.get("/")
    handle = "a2-second-" + uuid4().hex
    params = {"handle": handle, "project": usage_world.projects[1], "revision": uuid4()}
    async with usage_world.maker.begin() as db:
        await db.execute(text("INSERT INTO service_hostnames VALUES (:handle, :project)"), params)
        await db.execute(
            text("INSERT INTO service_routes VALUES (:project, :handle, :revision)"), params
        )
    second = await client.get("https://" + handle + ".agentspore.com/")
    assert first.cookies["__Host-visitor"] != second.cookies["__Host-visitor"]
    assert await gateway_redis.dbsize() == 2
    monkeypatch.setattr(UsageService, "today", staticmethod(lambda: TODAY + timedelta(days=1)))
    assert (await client.get("/")).status_code == 200
    assert await gateway_redis.dbsize() == 3


async def test_ipv6_mapped_and_trusted_proxy_addresses_use_the_same_canonical_hash(
    gateway_route, gateway_redis
):
    host = gateway_route["handle"] + ".agentspore.com"
    for peer in ["203.0.113.18", "::ffff:203.0.113.18", "127.0.0.1"]:
        async with AsyncClient(
            transport=ASGITransport(app=app, client=(peer, 4321)),
            base_url="https://" + host,
            headers={"user-agent": BROWSER, "accept": "text/html"},
        ) as client:
            assert (
                await client.get("/", headers={"x-forwarded-for": "203.0.113.18"})
            ).status_code == 200
    assert await gateway_redis.dbsize() == 1


async def test_disconnected_body_never_reaches_upstream(gateway_route, gateway_boundaries):
    messages = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", (gateway_route["handle"] + ".agentspore.com").encode())],
        },
        receive,
        send,
    )
    assert messages == [] and gateway_boundaries["requests"] == []


async def test_conditional_browser_request_gets_uncached_html(gateway_api):
    client, control = gateway_api
    response = await client.get(
        "/",
        headers={
            "if-none-match": '"synthetic-etag"',
            "if-modified-since": "Wed, 14 Oct 2026 12:00:00 GMT",
        },
    )
    assert response.status_code == 200
    assert response.text == SERVICE_HTML
    outgoing = control["requests"][0].headers
    assert "if-none-match" not in outgoing and "if-modified-since" not in outgoing


async def test_decoded_nul_path_is_rejected_before_database_checkout(
    gateway_route,
    gateway_boundaries,
    usage_world,
):
    checkouts = []
    engine = usage_world.maker.kw["bind"].sync_engine

    def capture(*args):
        checkouts.append(1)

    event.listen(engine, "checkout", capture)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="https://" + gateway_route["handle"] + ".agentspore.com",
        ) as client:
            response = await client.get("/%00")
    finally:
        event.remove(engine, "checkout", capture)
    assert response.status_code == 400
    assert checkouts == [] and gateway_boundaries["requests"] == []


async def seeded_gateway(handle, host_suffix=".agentspore.com"):
    return AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.18", 4321)),
        base_url="https://" + handle + host_suffix,
        headers={"user-agent": BROWSER, "accept": "text/html"},
    )


async def test_service_resolves_on_the_ru_mirror_domain(gateway_route, gateway_boundaries):
    inner = ServiceGatewayMiddleware(app, app.state, domains=["agentspore.com", "agentspore.ru"])
    async with AsyncClient(
        transport=ASGITransport(app=inner, client=("203.0.113.18", 4321)),
        base_url="https://" + gateway_route["handle"] + ".agentspore.ru",
        headers={"user-agent": BROWSER, "accept": "text/html"},
    ) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert gateway_boundaries["requests"][0].url.host == "svc-" + gateway_route["revision"].hex


async def test_service_stats_path_is_forwarded(gateway_api):
    client, gateway_boundaries = gateway_api
    response = await client.get("/stats")
    assert response.status_code == 200
    assert gateway_boundaries["requests"][0].url.path == "/stats"


async def test_unknown_handle_on_seeded_registry_is_404(gateway_boundaries):
    async with await seeded_gateway("family-safety-2") as client:
        response = await client.get("/")
    assert response.status_code == 404
    assert gateway_boundaries["requests"] == []


@pytest.mark.parametrize("handle", ["foundphone", "family-safety", "emergency-binder"])
async def test_retired_platform_service_is_404_without_an_upstream_request(
    gateway_boundaries, handle
):
    async with await seeded_gateway(handle) as client:
        response = await client.get("/")
        stats = await client.get("/stats")
    assert (response.status_code, stats.status_code) == (404, 404)
    assert gateway_boundaries["requests"] == []
