"""A4 P2a: Builder seam and its FakeBuilder."""

import pytest

from app.services.deployment.builder import Builder, BuildRequest, BuildResult, FakeBuilder
from app.services.deployment.errors import DeployRejectedError


async def test_fake_builder_records_and_returns(tmp_path):
    builder = FakeBuilder(BuildResult(image_ref="a4/x:1", image_bytes=123))
    assert isinstance(builder, Builder)
    request = BuildRequest(context_dir=tmp_path, tag="a4/x:1")
    assert await builder.build(request) == BuildResult(image_ref="a4/x:1", image_bytes=123)
    assert builder.requests == [request]


async def test_fake_builder_can_refuse(tmp_path):
    builder = FakeBuilder(error=DeployRejectedError("image_too_large"))
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=tmp_path, tag="t"))
    assert exc.value.reason_code == "image_too_large"
    assert len(builder.requests) == 1
