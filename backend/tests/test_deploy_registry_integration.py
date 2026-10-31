"""A4 P3b-2c-b: push to a real `registry:2` (Testcontainers) and read the image back."""

import time
from pathlib import Path

import httpx
import pytest
import yaml
from testcontainers.core.container import DockerContainer

from app.services.deployment.registry_push import RegistryPusher
from tests.test_deploy_registry_push import image_tar, sha  # noqa: F401 (fixture reuse)

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "a4-registry.compose.yml"
SERVICE = yaml.safe_load(COMPOSE.read_text())["services"]["a4-registry"]


@pytest.fixture(scope="module")
def registry_url():
    box = DockerContainer(SERVICE["image"]).with_exposed_ports(5000)
    for key, value in SERVICE["environment"].items():
        box.with_env(key, value)
    box.with_kwargs(user=SERVICE["user"], tmpfs={"/var/lib/registry": "uid=1000,gid=1000"})
    box.start()
    try:
        url = f"http://{box.get_container_host_ip()}:{box.get_exposed_port(5000)}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{url}/v2/", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        yield url
    finally:
        box.stop(force=True, delete_volume=True)


async def test_real_registry_stores_the_image_by_digest(image_tar, registry_url):  # noqa: F811 (pytest injects the imported fixture by parameter name)
    tar = image_tar(layers=(b"layer-one", b"layer-two"))
    host = registry_url.removeprefix("http://")
    push = RegistryPusher(registry_url, host)
    ref = await push.push(tar, "a4-it-1")
    digest = ref.rpartition("@")[2]
    assert ref == f"{host}/a4-it-1@{digest}"
    async with httpx.AsyncClient(base_url=registry_url, timeout=10) as client:
        manifest = await client.get(
            f"/v2/a4-it-1/manifests/{digest}",
            headers={"Accept": "application/vnd.oci.image.manifest.v1+json"},
        )
        assert manifest.status_code == 200
        assert sha(manifest.content) == digest
        layer = await client.get(f"/v2/a4-it-1/blobs/{sha(b'layer-two')}")
        assert layer.content == b"layer-two"
    assert await push.push(tar, "a4-it-1") == ref  # second push: blobs skipped, same ref


async def test_deleting_a_manifest_is_refused_with_deletes_disabled(image_tar, registry_url):  # noqa: F811 (pytest injects the imported fixture by parameter name)
    ref = await RegistryPusher(registry_url, "h").push(image_tar(), "a4-it-2")
    digest = ref.rpartition("@")[2]
    async with httpx.AsyncClient(base_url=registry_url, timeout=10) as client:
        assert (await client.delete(f"/v2/a4-it-2/manifests/{digest}")).status_code == 405
        assert (await client.delete(f"/v2/a4-it-2/blobs/{sha(b'layer-one')}")).status_code == 405
