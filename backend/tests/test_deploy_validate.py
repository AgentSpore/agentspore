"""A4 P3b-2c: `A4_BUILDER=buildkit` is validated when the worker starts, not at the first build."""

import pytest

from app.core.config import Settings
from app.services.deployment.buildkit import builder_from_settings, validate_builder_settings

TLS_FILES = ("ca.pem", "client.pem", "client-key.pem")


@pytest.fixture
def ready(tmp_path):
    tls = tmp_path / "tls"
    tls.mkdir()
    for name in TLS_FILES:
        (tls / name).write_text("pem")
    out = tmp_path / "out"
    out.mkdir()
    return dict(
        _env_file=None,
        a4_builder="buildkit",
        a4_buildkit_tls_dir=tls,
        a4_builder_out_dir=out,
    )


def test_valid_settings_pass_and_build_the_builder(ready):
    validate_builder_settings(Settings(**ready))
    assert builder_from_settings(Settings(**ready)) is not None


def test_fake_builder_skips_validation(tmp_path):
    cfg = Settings(_env_file=None, a4_buildkit_tls_dir=tmp_path / "missing")
    validate_builder_settings(cfg)


@pytest.mark.parametrize(
    "addr", ["buildkitd:1234", "http://buildkitd:1234", "tcp://buildkitd", "tcp://:1"]
)
def test_bad_addr_is_refused(ready, addr):
    with pytest.raises(RuntimeError, match="A4_BUILDKIT_ADDR"):
        validate_builder_settings(Settings(**ready, a4_buildkit_addr=addr))


@pytest.mark.parametrize("name", TLS_FILES)
def test_missing_tls_file_is_named_without_contents(ready, name):
    (ready["a4_buildkit_tls_dir"] / name).unlink()
    with pytest.raises(RuntimeError) as exc:
        builder_from_settings(Settings(**ready))
    assert name in str(exc.value) and "pem" not in str(exc.value).replace(name, "")


def test_unwritable_out_dir_is_refused(ready, tmp_path):
    ready["a4_builder_out_dir"] = tmp_path / "absent"
    with pytest.raises(RuntimeError, match="A4_BUILDER_OUT_DIR"):
        validate_builder_settings(Settings(**ready))
