"""A4 P2a: agentspore.toml parser (spec Q11) - defaults, strict keys, stable reason_code."""

import pytest

from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.manifest import Manifest, load_manifest


def test_manifest_absent_gives_defaults(tmp_path):
    manifest = load_manifest(tmp_path)
    assert manifest == Manifest()
    assert (manifest.port, manifest.health, manifest.build) == (8080, "/health", "dockerfile")
    assert (manifest.env, manifest.secrets, manifest.storage, manifest.egress) == (
        {},
        [],
        False,
        [],
    )


def test_manifest_full_roundtrip(tmp_path):
    (tmp_path / "agentspore.toml").write_text(
        'port = 3000\nhealth = "/up"\nbuild = "auto"\nsecrets = ["API_KEY", "DB_URL"]\n'
        'storage = true\negress = ["api.example.com"]\n[env]\nLOG_LEVEL = "info"\n'
    )
    manifest = load_manifest(tmp_path)
    assert manifest.port == 3000
    assert manifest.health == "/up"
    assert manifest.build == "auto"
    assert manifest.secrets == ["API_KEY", "DB_URL"]
    assert manifest.storage is True
    assert manifest.egress == ["api.example.com"]
    assert manifest.env == {"LOG_LEVEL": "info"}


@pytest.mark.parametrize(
    "body",
    [
        "surprise = 1\n",
        "port = 80\n",
        "port = 70000\n",
        'port = "8080"\n',
        'health = "up"\n',
        'build = "kaniko"\n',
        'secrets = ["lower"]\n',
        'secrets = ["A", "A"]\n',
        'secrets = { API_KEY = "hunter2" }\n',
        'secrets = ["API_KEY=hunter2"]\n',
        '[env]\nPORT = "1"\n',
        '[env]\nPUBLIC_URL = "x"\n',
        '[env]\nlower = "x"\n',
        "[env]\nA = 1\n",
        'egress = ["bad host"]\n',
        'storage = "yes"\n',
        "port = = 1\n",
    ],
)
def test_manifest_rejects_invalid(tmp_path, body):
    (tmp_path / "agentspore.toml").write_text(body)
    with pytest.raises(DeployRejectedError) as exc:
        load_manifest(tmp_path)
    assert exc.value.reason_code == "manifest_invalid"


def test_manifest_rejects_too_many_secrets_and_oversize(tmp_path):
    names = ", ".join(f'"S{i}"' for i in range(21))
    (tmp_path / "agentspore.toml").write_text(f"secrets = [{names}]\n")
    with pytest.raises(DeployRejectedError):
        load_manifest(tmp_path)
    (tmp_path / "agentspore.toml").write_text("# " + "x" * 70_000)
    with pytest.raises(DeployRejectedError):
        load_manifest(tmp_path)


def test_manifest_error_detail_never_echoes_values(tmp_path):
    (tmp_path / "agentspore.toml").write_text('secrets = ["API_KEY=hunter2"]\n')
    with pytest.raises(DeployRejectedError) as exc:
        load_manifest(tmp_path)
    assert "hunter2" not in str(exc.value)
