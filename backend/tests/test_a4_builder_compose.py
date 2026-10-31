"""Hardening gate for the A4 builder stack (`deploy/a4-builder.compose.yml`)."""

import ipaddress
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "a4-builder.compose.yml"
CONF = ROOT / "deploy" / "a4-builder"
RUNBOOK = ROOT / "docs" / "a4-builder-runbook.md"

BUILDER_SUBNET = ipaddress.IPv4Network("10.213.77.0/24")
BUILDER_IPS = {
    "mirror-gate": ipaddress.ip_address("10.213.77.10"),
    "pkg-mirror": ipaddress.ip_address("10.213.77.12"),
}
MIRROR_BASES = {"python", "node", "golang", "rust", "alpine", "debian", "busybox"}
MIRROR_LOCATIONS = ["= /v2/", "~ MIRROR_REGEX", "/"]
GATE = "a4-restart-gate"
CONTAINER = "a4-builder-buildkitd-1"
BUILDKITD_COMMAND = [
    "--addr=tcp://0.0.0.0:1234",
    "--tlscert=/run/secrets/a4_buildkit_cert",
    "--tlskey=/run/secrets/a4_buildkit_key",
    "--tlscacert=/run/secrets/a4_buildkit_ca",
    "--config=/etc/buildkit/buildkitd.toml",
    "--oci-worker-no-process-sandbox",
]


def _is_internal(name: str, net: dict) -> bool:
    # a4-control alone is external here (compose forbids `internal` with `external`); the main
    # compose file declares it internal and test_deploy_worker_compose.py asserts that.
    return net.get("internal") is True or (name == "a4-control" and net.get("external") is True)


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


def test_buildkitd_runs_under_gvisor_without_privilege(services):
    svc = services["buildkitd"]
    assert svc["runtime"] == "runsc"
    assert not svc.get("privileged", False)
    assert "ALL" in svc.get("cap_drop", [])
    assert svc.get("user") not in (None, "0", "root", 0)
    assert set(svc.get("cap_add", [])) <= {"SETUID", "SETGID"}
    assert svc["deploy"]["resources"]["limits"]["pids"] == 512
    assert not [k for k in (svc.get("environment") or {}) if "PROXY" in str(k).upper()]


def test_pids_are_limited_in_one_place_only(services):
    # Compose rejects distinct `pids_limit` and `deploy.resources.limits.pids` values.
    for name, svc in services.items():
        if "pids" in svc.get("deploy", {}).get("resources", {}).get("limits", {}):
            assert "pids_limit" not in svc, name


def test_builder_network_is_pinned_and_peers_have_static_ips(stack, services):
    # Docker's embedded DNS (127.0.0.11) refuses queries under runsc: no name lookup, only IPs.
    net = stack["networks"]["a4-builder"]
    subnet = ipaddress.ip_network(net["ipam"]["config"][0]["subnet"])
    assert subnet == BUILDER_SUBNET
    ips = {
        n: ipaddress.ip_address(services[n]["networks"]["a4-builder"]["ipv4_address"])
        for n in ("mirror-gate", "pkg-mirror")
    }
    assert ips == BUILDER_IPS and all(ip in subnet for ip in ips.values())
    hosts = {h.split(":")[0]: h.split(":")[1] for h in services["buildkitd"]["extra_hosts"]}
    assert hosts == {n: str(ip) for n, ip in ips.items()}


def test_networks_disable_ipv6_and_dynamic_range_excludes_static_ips(stack):
    # Prod hosts listen on [::]: IPv6 on these bridges is off explicitly, not by Docker default.
    for name, net in stack["networks"].items():
        if name == "a4-control":  # created and configured by the main compose file
            assert net["external"] is True
        else:
            assert net["enable_ipv6"] is False, name
    ip_range = ipaddress.IPv4Network(
        stack["networks"]["a4-builder"]["ipam"]["config"][0]["ip_range"]
    )
    assert ip_range.subnet_of(BUILDER_SUBNET)
    assert not any(ip in ip_range for ip in BUILDER_IPS.values())


def test_buildkitd_command_is_exact_and_requires_mtls(services):
    # RUN shares the daemon's network namespace: a tcp listener needs mTLS.
    assert services["buildkitd"]["command"] == BUILDKITD_COMMAND
    assert set(services["buildkitd"]["secrets"]) == {
        "a4_buildkit_cert",
        "a4_buildkit_key",
        "a4_buildkit_ca",
    }


def test_buildkitd_limits_one_cpu_and_memory_covers_the_state_tmpfs(services):
    limits = services["buildkitd"]["deploy"]["resources"]["limits"]
    assert float(limits["cpus"]) == 1.0
    assert limits["memory"] == "6G"  # 4g tmpfs + 2G working set


def test_buildkitd_state_is_a_capped_tmpfs_so_a_restart_wipes_it(services):
    svc = services["buildkitd"]
    assert svc["restart"] == "no"
    (entry,) = [t for t in svc["tmpfs"] if t.startswith("/home/user/.local/share/buildkit:")]
    opts = entry.split(":", 1)[1].split(",")
    assert {"uid=1000", "gid=1000", "mode=0700", "size=4g", "exec"} <= set(opts)
    assert not {"noexec", "nosuid", "nodev"} & set(opts)
    assert not [v for v in svc["volumes"] if "/.local/share/buildkit" in str(v)]
    assert not [
        v for v in svc["volumes"] if v.startswith(("/", "~", ".")) and ":" in v and ":ro" not in v
    ]


def test_no_service_mounts_docker_socket_or_host_namespaces(services):
    for name, svc in services.items():
        if name != GATE:
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


def test_only_package_mirror_and_registry_reach_the_internet(stack, services):
    routed = {
        name
        for name, svc in services.items()
        if any(not _is_internal(n, stack["networks"][n]) for n in svc["networks"])
    }
    assert routed == {"pkg-mirror", "mirror"}


def test_buildkitd_sees_only_gate_and_package_mirror(services):
    peers = {
        name
        for name, svc in services.items()
        if name != "buildkitd" and set(svc["networks"]) & set(services["buildkitd"]["networks"])
    }
    assert peers == {"mirror-gate", "pkg-mirror"}


def test_buildkitd_waits_for_healthy_gate_and_package_mirror(services):
    deps = services["buildkitd"]["depends_on"]
    assert deps["mirror-gate"]["condition"] == "service_healthy"
    assert deps["pkg-mirror"]["condition"] == "service_healthy"
    assert services["pkg-mirror"]["healthcheck"]["test"]


def test_builder_bridge_has_no_host_address(stack):
    # Isolated internal bridge: no gateway address, so RUN cannot reach host listeners on 0.0.0.0.
    net = stack["networks"]["a4-builder"]
    assert net["internal"] is True
    assert net["driver_opts"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"


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
    assert (
        "--oci-worker-no-process-sandbox" in text
        and "restarts the daemon after every build" in text
    )
    assert "docker restart a4-builder-buildkitd-1" in text and "A4_DOCKER_GID" in text
    assert "a4-restart-gate" in text and "tool_failed" in text
    assert "--host-uds=none" in text
    assert "mktemp -d" in text and "shred -u" in text
    assert "network.host" in text
    assert "cache is full" in text
    assert "10.213.77.10" in text


def test_runbook_records_p3b_rules_and_bridge_checks():
    text = RUNBOOK.read_text()
    assert "`registry:2`" not in text and "`registry:3`" in text
    assert "ptrace" in text and "type=oci,dest=" in text and "worker pushes" in text
    assert "resolv.conf" in text and "by IP" in text
    assert "ip -4 addr show dev br-" in text and "gateway_mode_ipv4" in text
    assert "ip -6 addr show dev br-" in text and "10.213.77.1 $p" not in text


def _gate_locations() -> list[tuple[str, str]]:
    """(modifier+pattern, body) of every `location` block in the gate config."""
    text = (CONF / "restart-gate.conf").read_text()
    found = re.findall(r'location\s+(~\s+"[^"]+"|\S+)\s*\{(.*?)\n    \}', text, re.S)
    return [(m, b) for m, b in found]


def test_gate_is_hardened_on_control_network_only_and_mounts_the_socket_read_only(stack, services):
    svc = services[GATE]
    assert svc["networks"] == ["a4-control"]
    assert "a4-builder" not in svc["networks"]
    assert svc["read_only"] is True and "ALL" in svc["cap_drop"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert not svc.get("cap_add") and not svc.get("privileged", False) and "ports" not in svc
    (sock,) = [
        v for v in svc["volumes"] if isinstance(v, dict) and v["target"].endswith("docker.sock")
    ]
    assert sock["read_only"] is True and sock["bind"]["create_host_path"] is False
    assert svc["group_add"][0].startswith("${A4_DOCKER_GID:?")
    assert stack["networks"]["a4-control"]["external"] is True
    assert [n for n, s in services.items() if "a4-control" in s["networks"]] == [GATE]


RESTART_LOCATION = '~ "^/v1\\.[0-9]{2}/containers/a4-builder-buildkitd-1/restart$"'
INSPECT_LOCATION = '~ "^/v1\\.[0-9]{2}/containers/a4-builder-buildkitd-1/json$"'


def test_gate_default_denies_and_allows_only_restart_and_inspect_of_buildkitd():
    locations = _gate_locations()
    assert [m for m, _ in locations] == [RESTART_LOCATION, INSPECT_LOCATION, "/"]
    bodies = dict(locations)
    assert bodies["/"].strip() == "return 403;"
    restart, inspect = bodies[RESTART_LOCATION], bodies[INSPECT_LOCATION]
    for body in (restart, inspect):
        assert "proxy_pass http://unix:/var/run/docker.sock;" in body
    assert "$request_method != POST" in restart and "$request_method != GET" in inspect
    assert "proxy_pass_request_body off;" in restart
    assert '$args !~ "^(t=[0-9]{1,2})?$"' in restart and '$args != ""' in inspect
    assert "client_max_body_size 1;" in (CONF / "restart-gate.conf").read_text()


def test_gate_regexes_from_the_config_match_only_the_two_calls():
    # Patterns come from restart-gate.conf itself, not from a copy kept in this file.
    regexes = [re.compile(m.removeprefix("~ ").strip('"')) for m, _ in _gate_locations()[:2]]
    ok = [f"/v1.44/containers/{CONTAINER}/restart", f"/v1.51/containers/{CONTAINER}/json"]
    bad = [
        "/containers/create",
        "/v1.44/containers/create",
        f"/v1.44/containers/{CONTAINER}/exec",
        f"/v1.44/containers/{CONTAINER}/restart/x",
        f"/v1.44/containers/{CONTAINER}x/restart",
        f"/v1.4/containers/{CONTAINER}/restart",
        "/v1.44/containers/other/restart",
        f"/v1.44/containers/{CONTAINER}/kill",
        f"/v1.44/containers/{CONTAINER}/restart|/v1.44/containers/create",
    ]
    assert [sum(bool(r.search(p)) for r in regexes) for p in ok] == [1, 1]
    assert not [p for p in bad if any(r.search(p) for r in regexes)]


def test_buildkitd_healthcheck_checks_mtls_so_the_gate_json_means_ready(services):
    test = services["buildkitd"]["healthcheck"]["test"]
    assert test[:2] == ["CMD", "buildctl"] and test[-2:] == ["debug", "workers"]
    assert "--tlsservername=buildkitd" in test and "--tlskey=/run/secrets/a4_buildkit_key" in test


def test_buildkitd_rootfs_is_read_only_with_tmpfs_for_every_rootless_write_path(services):
    # moby/buildkit v0.23.2 Dockerfile, rootless stage: XDG_RUNTIME_DIR=/run/user/1000,
    # TMPDIR=/home/user/.local/tmp, state /home/user/.local/share/buildkit.
    svc = services["buildkitd"]
    assert svc["read_only"] is True
    paths = {t.split(":", 1)[0] for t in svc["tmpfs"]}
    assert paths == {
        "/run/user/1000",
        "/tmp",
        "/home/user/.local/tmp",
        "/home/user/.local/share/buildkit",
    }
