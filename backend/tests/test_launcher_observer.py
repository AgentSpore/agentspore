"""LauncherObserver: unknown is never absent; only a successful listing without the ns counts."""

from uuid import uuid4

import httpx
import pytest

from app.schemas.deployments import (
    DeploymentLease,
    SandboxNamespaceAbsenceProof,
    SandboxStopProof,
)
from app.services.deployment.launcher_client import LauncherClient
from app.services.deployment.launcher_observer import LauncherObserver

CID = "e" * 64


@pytest.fixture
def lease() -> DeploymentLease:
    return DeploymentLease(deployment_id=uuid4(), worker_id=uuid4(), generation=2)


class Launcher:
    """Fake a4-launcher: `listing` None means unreachable; records mutating calls."""

    def __init__(self, listing, fail_listing=False):
        self.listing, self.fail_listing, self.calls = listing, fail_listing, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            if self.fail_listing:
                return httpx.Response(502, json={"reason": "docker_error"})
            return httpx.Response(200, json={"runtimes": self.listing})
        self.calls.append((request.method, request.url.path.rsplit("/", 1)[-1]))
        return httpx.Response(200, json={"ok": True})

    def observer(self, status="recovering") -> LauncherObserver:
        client = LauncherClient("http://l:8080", transport=httpx.MockTransport(self))

        async def status_of(_):
            return status

        return LauncherObserver(client, status_of)


def entry(lease, state="running"):
    return {"namespace": lease.sandbox_namespace, "container_id": CID, "state": state}


@pytest.mark.asyncio
async def test_listing_without_namespace_and_no_id_is_absent(lease):
    fake = Launcher([])
    proof = await fake.observer()(lease, None)
    assert proof == SandboxNamespaceAbsenceProof(lease=lease, namespace=lease.sandbox_namespace)
    assert fake.calls == [("DELETE", lease.sandbox_namespace)]


@pytest.mark.asyncio
async def test_launcher_error_is_unknown_not_absent(lease):
    fake = Launcher([], fail_listing=True)
    assert await fake.observer()(lease, None) is None
    assert await fake.observer()(lease, CID) is None
    assert fake.calls == []


@pytest.mark.asyncio
async def test_launcher_down_is_unknown_not_absent(lease):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = LauncherClient("http://l:8080", transport=httpx.MockTransport(down))

    async def status_of(_):
        return None

    observer = LauncherObserver(client, status_of)
    assert await observer(lease, None) is None


@pytest.mark.asyncio
async def test_present_container_without_recorded_id_is_stopped_and_adopted(lease):
    fake = Launcher([entry(lease)])
    proof = await fake.observer()(lease, None)
    assert proof == SandboxStopProof(lease=lease, sandbox_id=CID)
    assert fake.calls == [("POST", "stop")]


@pytest.mark.asyncio
async def test_recorded_id_is_stopped_then_proved(lease):
    fake = Launcher([entry(lease)])
    proof = await fake.observer()(lease, CID)
    assert proof == SandboxStopProof(lease=lease, sandbox_id=CID)
    assert fake.calls == [("POST", "stop")]


@pytest.mark.asyncio
async def test_recorded_id_with_namespace_gone_is_proved_after_network_sweep(lease):
    fake = Launcher([])
    assert await fake.observer()(lease, CID) == SandboxStopProof(lease=lease, sandbox_id=CID)
    assert fake.calls == [("DELETE", lease.sandbox_namespace)]


@pytest.mark.asyncio
async def test_active_revision_is_never_stopped(lease):
    fake = Launcher([entry(lease)])
    proof = await fake.observer(status="active")(lease, CID)
    assert proof == SandboxStopProof(lease=lease, sandbox_id=CID)
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "present,recorded", [(False, False), (False, True), (True, False), (True, True)]
)
@pytest.mark.parametrize("content", [b"not-json", b'{"ok":false}'])
async def test_unconfirmed_cleanup_never_produces_recovery_proof(lease, present, recorded, content):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"runtimes": [entry(lease)] if present else []})
        return httpx.Response(200, content=content)

    async def status_of(_):
        return "recovering"

    client = LauncherClient("http://launcher:8080", transport=httpx.MockTransport(handler))
    try:
        observer = LauncherObserver(client, status_of)
        assert await observer(lease, CID if recorded else None) is None
    finally:
        await client.aclose()
