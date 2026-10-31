"""Hardening gate for the A4 builder stack (`deploy/a4-builder.compose.yml`)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "a4-builder.compose.yml"
CONF = ROOT / "deploy" / "a4-builder"
RUNBOOK = ROOT / "docs" / "a4-builder-runbook.md"

EGRESS_DOMAINS = {
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "proxy.golang.org",
    "crates.io",
    "index.crates.io",
    "static.crates.io",
}
DENIED_DST = {
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "64:ff9b::/96",
    "ff00::/8",
}
PLATFORM_DST = {"212.237.217.228/32", "172.17.0.0/16"}
HTTP_ACCESS = [
    "deny ip_literal",
    "deny deny_dst",
    "deny platform_dst",
    "deny !CONNECT",
    "deny CONNECT !SSL_ports",
    "allow CONNECT egress_ok SSL_ports",
    "deny all",
]
MIRROR_BASES = {"python", "node", "golang", "rust", "alpine", "debian", "busybox"}
MIRROR_LOCATIONS = ["= /v2/", "~ MIRROR_REGEX", "/"]
BUILDKITD_COMMAND = [
    "--addr=tcp://0.0.0.0:1234",
    "--tlscert=/run/secrets/a4_buildkit_cert",
    "--tlskey=/run/secrets/a4_buildkit_key",
    "--tlscacert=/run/secrets/a4_buildkit_ca",
    "--config=/etc/buildkit/buildkitd.toml",
    "--oci-worker-no-process-sandbox",
]


def _directives(path: Path, name: str) -> list[list[str]]:
    """Whitespace-split arguments of every non-comment `name` directive."""
    rows = []
    for raw in path.read_text().splitlines():
        parts = raw.split("#", 1)[0].split()
        if parts[:1] == [name]:
            rows.append(parts[1:])
    return rows


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
    assert not [k for k in (svc.get("environment") or {}) if "PROXY" in str(k).upper()]


def test_buildkitd_command_is_exact_and_requires_mtls(services):
    # RUN shares the daemon's network namespace: a tcp listener needs mTLS.
    assert services["buildkitd"]["command"] == BUILDKITD_COMMAND
    assert set(services["buildkitd"]["secrets"]) == {
        "a4_buildkit_cert",
        "a4_buildkit_key",
        "a4_buildkit_ca",
    }


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


def test_buildkitd_sees_only_gate_and_proxy(services):
    peers = {
        name
        for name, svc in services.items()
        if name != "buildkitd" and set(svc["networks"]) & set(services["buildkitd"]["networks"])
    }
    assert peers == {"mirror-gate", "egress-proxy"}


def test_buildkitd_waits_for_healthy_gate_and_proxy(services):
    deps = services["buildkitd"]["depends_on"]
    assert deps["mirror-gate"]["condition"] == "service_healthy"
    assert deps["egress-proxy"]["condition"] == "service_healthy"
    assert services["egress-proxy"]["healthcheck"]["test"]


def test_proxy_allowlist_is_exactly_the_registries_without_reverse_lookup():
    rows = [
        r for r in _directives(CONF / "squid.conf", "acl") if r[:2] == ["egress_ok", "dstdomain"]
    ]
    assert len(rows) == 1 and rows[0][2] == "-n"
    assert set(rows[0][3:]) == EGRESS_DOMAINS


def test_proxy_denies_ip_literals_and_internal_ranges():
    acls = _directives(CONF / "squid.conf", "acl")
    denied = {c for r in acls if r[:2] == ["deny_dst", "dst"] for c in r[2:]}
    assert DENIED_DST <= denied
    platform = {c for r in acls if r[:2] == ["platform_dst", "dst"] for c in r[2:]}
    assert PLATFORM_DST <= platform
    literal = [r for r in acls if r[:2] == ["ip_literal", "dstdom_regex"]]
    assert literal and literal[0][2] == "-n"
    assert not [r for r in acls if r[0] == "deny_dst" and r[1] != "dst"]


def test_proxy_http_access_order_is_exact():
    rules = [" ".join(r) for r in _directives(CONF / "squid.conf", "http_access")]
    assert rules == HTTP_ACCESS


def test_proxy_has_no_obsolete_directives():
    assert not _directives(CONF / "squid.conf", "dns_v4_first")


def test_buildkitd_pulls_docker_io_only_through_mirror_gate():
    toml = (CONF / "buildkitd.toml").read_text()
    assert re.search(r'\[registry\."docker\.io"\]\s+mirrors = \["mirror-gate:5000"\]', toml)
    assert re.search(r'\[registry\."mirror-gate:5000"\]\s+http = true', toml)


def test_mirror_gate_has_exactly_three_locations_and_one_upstream():
    conf = (CONF / "mirror-gate.conf").read_text()
    locations = re.findall(r"^\s*location\s+(.+?)\s*\{\s*$", conf, re.M)
    shape = [re.sub(r'^~ ".*"$', "~ MIRROR_REGEX", loc) for loc in locations]
    assert shape == MIRROR_LOCATIONS
    assert conf.count("proxy_pass") == 1
    assert "limit_except GET HEAD" in conf
    assert re.search(r"location / \{\s+return 403;", conf)


def test_mirror_gate_allows_listed_bases_and_manifests_only_by_digest():
    conf = (CONF / "mirror-gate.conf").read_text()
    match = re.search(r'location ~ "\^/v2/library/\(([^)]*)\)/(.*)\$"', conf)
    assert match and set(match.group(1).split("|")) == MIRROR_BASES
    assert match.group(2) == r"(manifests/sha256:[a-f0-9]{64}|blobs/sha256:[a-f0-9]{64})"


def test_mirror_registry_is_pull_through_with_expiry():
    cfg = yaml.safe_load((CONF / "registry-config.yml").read_text())
    assert cfg["proxy"]["remoteurl"] == "https://registry-1.docker.io"
    assert cfg["proxy"]["ttl"] == "12h"
    assert cfg["storage"]["delete"]["enabled"] is False


def test_runbook_states_recycle_rules_and_network_limits():
    text = RUNBOOK.read_text()
    assert "--oci-worker-no-process-sandbox" in text and "destroyed after every build" in text
    assert "up -d --force-recreate -V" in text
    assert "network.host" in text
    assert "cache is full" in text
    assert "CONNECT 203.0.113.7:443" in text
