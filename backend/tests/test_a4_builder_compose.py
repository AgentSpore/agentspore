"""Hardening gate for the A4 builder stack (`deploy/a4-builder.compose.yml`)."""

import re
import subprocess
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
IP_LITERAL = ["-n", "^[0-9.]+$", "^[0-9a-f]*:[0-9a-f:.]*$", r"^\["]
PLATFORM_INCLUDE = "/etc/squid/platform-dst.conf"
HTTP_ACCESS = [
    "deny ip_literal",
    "deny deny_dst",
    "deny platform_dst",
    "deny !CONNECT",
    "deny CONNECT !SSL_ports",
    "allow CONNECT egress_ok SSL_ports",
    "deny all",
]
SSL_BUMP = ["peek step1", "terminate step2 !sni_present", "splice allowed_sni", "terminate all"]
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
        if "build" in svc:
            dockerfile = (CONF / Path(svc["build"]["dockerfile"]).name).read_text()
            assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$", dockerfile, re.M), name
        else:
            assert re.search(r"@sha256:[0-9a-f]{64}$", svc["image"]), name


def test_egress_proxy_is_built_with_openssl_for_sni_peek(services):
    # The stock ubuntu/squid image links GnuTLS, which cannot ssl_bump; squid-openssl can.
    svc = services["egress-proxy"]
    assert svc["build"]["dockerfile"] == "Dockerfile.squid"
    dockerfile = (CONF / "Dockerfile.squid").read_text()
    assert "squid-openssl" in dockerfile and "USER proxy" in dockerfile
    entry = (CONF / "squid-entrypoint.sh").read_text()
    assert "/tmp/ssl" in entry and "BEGIN" not in entry
    assert not list(CONF.glob("*.pem")) and not list(CONF.glob("*.key"))


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


def test_proxy_checks_tls_sni_by_peek_and_splice_without_interception():
    conf = CONF / "squid.conf"
    port = next(r for r in _directives(conf, "http_port") if r[0] == "3128")
    assert "ssl-bump" in port and "generate-host-certificates=off" in port
    assert any(a.startswith("cert=/tmp/ssl/") for a in port)
    rules = [" ".join(r) for r in _directives(conf, "ssl_bump")]
    assert rules == SSL_BUMP
    acls = _directives(conf, "acl")
    assert ["step1", "at_step", "SslBump1"] in acls
    sni = [r for r in acls if r[:2] == ["allowed_sni", "ssl::server_name"]]
    # Without --client-requested a missing SNI falls back to the CONNECT target and matches.
    assert len(sni) == 1 and sni[0][2] == "--client-requested"
    assert set(sni[0][3:]) == EGRESS_DOMAINS
    # server_name never yields "none" (it falls back to the CONNECT target): the helper sees the SNI.
    assert ["step2", "at_step", "SslBump2"] in acls
    assert ["sni_present", "external", "sni_check"] in acls
    helper = next(r for r in _directives(conf, "external_acl_type") if r[0] == "sni_check")
    assert "%ssl::>sni" in helper and helper[-1] == "/usr/local/bin/sni-present.sh"
    assert "ttl=0" in helper and "negative_ttl=0" in helper


def test_sni_helper_rejects_absent_sni_and_is_shipped_in_the_image():
    script = CONF / "sni-present.sh"
    lines = "- -\n\npypi.org -\npypi.org -\n"
    run = subprocess.run(["sh", str(script)], input=lines, capture_output=True, text=True, timeout=10)
    assert run.stdout.split() == ["ERR", "ERR", "OK", "OK"]
    dockerfile = (CONF / "Dockerfile.squid").read_text()
    assert "COPY sni-present.sh /usr/local/bin/sni-present.sh" in dockerfile


def test_squid_is_pinned_to_an_exact_version():
    dockerfile = (CONF / "Dockerfile.squid").read_text()
    assert re.search(r"squid-openssl=\d+\.\d+-\S+", dockerfile)


def test_platform_file_is_untracked_mounted_strictly_and_checked_by_healthcheck(services):
    svc = services["egress-proxy"]
    mounts = [v for v in svc["volumes"] if isinstance(v, dict) and v["target"] == PLATFORM_INCLUDE]
    assert len(mounts) == 1
    mount = mounts[0]
    assert mount["source"] == "./a4-builder/platform-dst.conf" and mount["read_only"] is True
    assert mount["bind"]["create_host_path"] is False, "a missing file must fail the start"
    assert (CONF / "platform-dst.conf.example").is_file()
    assert (CONF / ".gitignore").read_text().split() == ["platform-dst.conf"]
    health = " ".join(svc["healthcheck"]["test"])
    assert "platform-dst.conf" in health and "127.0.0.1" in health


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
    literal = [r[2:] for r in acls if r[:2] == ["ip_literal", "dstdom_regex"]]
    assert literal == [IP_LITERAL]
    assert not [r for r in acls if r[0] == "deny_dst" and r[1] != "dst"]


def test_platform_destinations_come_from_an_operator_file_before_the_rules(services):
    lines = [ln.split("#", 1)[0].split() for ln in (CONF / "squid.conf").read_text().splitlines()]
    include = next(i for i, p in enumerate(lines) if p == ["include", PLATFORM_INCLUDE])
    first_rule = next(i for i, p in enumerate(lines) if p[:1] == ["http_access"])
    anchor = next(i for i, p in enumerate(lines) if p[:3] == ["acl", "platform_dst", "dst"])
    assert anchor < include < first_rule
    shipped = _directives(CONF / "platform-dst.conf.example", "acl")
    assert not shipped, "host addresses are written per host, never shipped"
    shared = [p for p in CONF.iterdir() if p.is_file() and p.name != "platform-dst.conf"]
    assert "212.237.217.228" not in "".join(p.read_text() for p in shared)


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


def test_mirror_registry_is_v3_pull_through_with_expiry(services):
    # proxy.ttl exists only in distribution v3 (config under /etc/distribution); v2 drops it.
    volumes = services["mirror"]["volumes"]
    assert "./a4-builder/registry-config.yml:/etc/distribution/config.yml:ro" in volumes
    assert "# registry:3." in COMPOSE.read_text()
    cfg = yaml.safe_load((CONF / "registry-config.yml").read_text())
    assert cfg["proxy"]["remoteurl"] == "https://registry-1.docker.io"
    assert cfg["proxy"]["ttl"] == "12h"
    assert cfg["storage"]["delete"]["enabled"] is False


def test_runbook_states_recycle_rules_and_network_limits():
    text = RUNBOOK.read_text()
    assert "--oci-worker-no-process-sandbox" in text and "destroyed after every build" in text
    assert "up -d --force-recreate --no-deps -V buildkitd" in text
    assert "--host-uds=none" in text
    assert "-V` does nothing for the mirror" in text
    assert "mktemp -d" in text and "shred -u" in text
    assert "network.host" in text
    assert "cache is full" in text
    assert "CONNECT 203.0.113.7:443" in text


def test_runbook_records_p3b_rules_residual_risk_and_sni_checks():
    text = RUNBOOK.read_text()
    assert "`registry:2`" not in text and "`registry:3`" in text
    assert "ptrace" in text and "type=oci,dest=" in text and "worker pushes" in text
    assert "## Accepted residual risk" in text
    assert "domain fronting" in text and "P3c" in text and "devpi" in text
    assert "-servername example.com" in text
    assert "platform-dst.conf.example" in text
    assert "-noservername" in text and "squid -v" in text
    assert re.search(r"\bECH\b", text) and "inner SNI" in text
