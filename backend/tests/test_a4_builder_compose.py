"""Hardening gate for the A4 builder stack (`deploy/a4-builder.compose.yml`)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "a4-builder.compose.yml"
CONF = ROOT / "deploy" / "a4-builder"

EGRESS_DOMAINS = {
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "proxy.golang.org",
    "crates.io",
}
DENIED_DST = {
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "169.254.169.254/32",
}
MIRROR_BASES = {"python", "node", "golang", "rust", "alpine", "debian", "busybox"}


@pytest.fixture(scope="module")
def stack() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


@pytest.fixture(scope="module")
def services(stack) -> dict:
    return stack["services"]


def test_every_image_is_pinned_by_digest(services):
    for name, svc in services.items():
        assert re.search(r"@sha256:[0-9a-f]{64}$", svc["image"]), name


def test_buildkitd_runs_under_gvisor_without_privilege(services):
    svc = services["buildkitd"]
    assert svc["runtime"] == "runsc"
    assert not svc.get("privileged", False)
    assert "ALL" in svc.get("cap_drop", [])
    assert svc.get("user") not in (None, "0", "root", 0)
    assert set(svc.get("cap_add", [])) <= {"SETUID", "SETGID"}
    assert svc["pids_limit"] > 0


def test_buildkitd_limits_one_cpu_two_gib(services):
    limits = services["buildkitd"]["deploy"]["resources"]["limits"]
    assert float(limits["cpus"]) == 1.0
    assert limits["memory"] == "2G"


def test_buildkitd_is_disposable_no_cache_volume(services):
    svc = services["buildkitd"]
    assert svc["restart"] == "no"
    state = [v for v in svc["volumes"] if "/.local/share/buildkit" in v]
    assert state == ["/home/user/.local/share/buildkit"], "state must be an anonymous volume"
    assert not [
        v for v in svc["volumes"] if v.startswith(("/", "~", ".")) and ":" in v and ":ro" not in v
    ]


def test_no_service_mounts_docker_socket_or_host_namespaces(services):
    for name, svc in services.items():
        assert "docker.sock" not in yaml.dump(svc.get("volumes", [])), name
        assert not svc.get("privileged", False), name
        assert svc.get("network_mode") not in ("host",), name
        assert svc.get("pid") != "host", name
        assert "ports" not in svc, name


def test_every_service_drops_caps_and_blocks_privilege_gain(services):
    for name, svc in services.items():
        assert "ALL" in svc["cap_drop"], name
        assert "no-new-privileges:true" in svc["security_opt"] or name == "buildkitd", name


def test_buildkitd_only_on_internal_networks(stack, services):
    nets = services["buildkitd"]["networks"]
    assert nets
    for net in nets:
        assert stack["networks"][net].get("internal") is True, net


def test_only_proxy_and_registry_reach_the_internet(stack, services):
    routed = {
        name
        for name, svc in services.items()
        if any(not stack["networks"][n].get("internal") for n in svc["networks"])
    }
    assert routed == {"egress-proxy", "mirror"}


def test_buildkitd_sees_only_gate_and_proxy(stack, services):
    peers = {
        name
        for name, svc in services.items()
        if name != "buildkitd" and set(svc["networks"]) & set(services["buildkitd"]["networks"])
    }
    assert peers == {"mirror-gate", "egress-proxy"}


def test_proxy_allowlist_exact_domains_and_private_denies():
    conf = (CONF / "squid.conf").read_text()
    allowed = {
        d
        for line in conf.splitlines()
        if line.startswith("acl egress_ok dstdomain")
        for d in line.split()[3:]
    }
    assert EGRESS_DOMAINS <= allowed
    assert not [d for d in allowed if d.startswith(".") or d == "*"]
    for cidr in DENIED_DST:
        assert cidr in conf
    rules = [line for line in conf.splitlines() if line.startswith("http_access")]
    assert rules.index("http_access deny deny_dst") < min(
        i for i, r in enumerate(rules) if r.startswith("http_access allow")
    )
    assert rules[-1] == "http_access deny all"


def test_buildkitd_pulls_docker_io_only_through_mirror_gate():
    toml = (CONF / "buildkitd.toml").read_text()
    assert re.search(r'\[registry\."docker\.io"\]\s+mirrors = \["mirror-gate:5000"\]', toml)
    assert re.search(r'\[registry\."mirror-gate:5000"\]\s+http = true', toml)


def test_mirror_gate_allows_only_listed_base_repositories():
    conf = (CONF / "mirror-gate.conf").read_text()
    match = re.search(r'location ~ "\^/v2/library/\(([^)]*)\)/', conf)
    assert match and set(match.group(1).split("|")) == MIRROR_BASES
    assert re.search(r"location / \{\s+return 403;", conf)
    assert "limit_except GET HEAD" in conf


def test_mirror_registry_is_pull_through_read_only():
    cfg = yaml.safe_load((CONF / "registry-config.yml").read_text())
    assert cfg["proxy"]["remoteurl"] == "https://registry-1.docker.io"
    assert cfg["storage"]["delete"]["enabled"] is False
