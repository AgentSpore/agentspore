"""Static gate for the A4 package mirrors (`pkg-mirror.conf`, service `pkg-mirror`)."""

import ipaddress
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "a4-builder.compose.yml"
CONF = ROOT / "deploy" / "a4-builder" / "pkg-mirror.conf"
RUNBOOK = ROOT / "docs" / "a4-builder-runbook.md"

MIRROR_IP = "10.213.77.12"
PORTS = {8081: "pypi", 8082: "npm", 8083: "go", 8084: "crates"}
UPSTREAM_HOSTS = {
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "proxy.golang.org",
    "sum.golang.org",
    "index.crates.io",
    "static.crates.io",
}
METHOD_GUARD = "if ($request_method !~ ^(GET|HEAD)$) { return 405; }"


def _servers(text: str) -> dict[int, str]:
    """Body of each top-level `server { ... }` block, keyed by its listen port."""
    out = {}
    for block in re.findall(r"^server \{\n(.*?)^\}", text, re.M | re.S):
        listen = re.search(r"listen (\d+);", block)
        assert listen, block
        out[int(listen.group(1))] = block
    return out


@pytest.fixture(scope="module")
def conf() -> str:
    return CONF.read_text()


@pytest.fixture(scope="module")
def svc() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]["pkg-mirror"]


def test_one_server_per_ecosystem_each_ending_in_default_deny(conf):
    servers = _servers(conf)
    assert set(servers) == set(PORTS)
    for port, block in servers.items():
        assert re.search(r"location / \{ return 403; \}\s*$", block), port


def test_only_get_and_head_are_served_and_nothing_proxies_connect(conf):
    for port, block in _servers(conf).items():
        assert METHOD_GUARD in block, port
    code = re.sub(r"(?m)^\s*#.*$", "", conf)
    assert "CONNECT" not in code and not re.search(r"\bproxy_connect\s", code)
    assert "dav_methods" not in code


def test_upstream_hosts_are_literals_and_exactly_the_registries(conf):
    targets = re.findall(r"^\s*proxy_pass (\S+);", conf, re.M)
    assert targets
    for target in targets:
        host = re.match(r"https://([a-z0-9.]+)[/$]", target)
        assert host and host.group(1) in UPSTREAM_HOSTS, target
    assert {t.split("/")[2].split("$")[0] for t in targets} == UPSTREAM_HOSTS
    assert not re.search(r"proxy_pass\s+\$|proxy_pass http://", conf)


def test_host_header_is_pinned_per_location_and_matches_the_upstream(conf):
    for loc in re.findall(r"location [^{]+\{(.*?)\n    \}", conf, re.S):
        target = re.search(r"proxy_pass https://([^/$]+)", loc)
        if target:
            assert f"proxy_set_header Host {target.group(1)};" in loc, loc


def test_nothing_from_the_client_reaches_the_upstream(conf):
    assert "proxy_pass_request_headers off;" in conf
    assert "proxy_pass_request_body off;" in conf
    assert not re.search(r"proxy_pass [^;]*\$(args|request_uri|http_|host|arg_)", conf)
    assert not re.search(r"proxy_set_header \S+ \$", conf)
    assert "client_max_body_size 1k;" in conf


def test_upstream_cookies_neither_block_caching_nor_reach_clients(conf):
    # Cloudflare-fronted registries send Set-Cookie, which makes nginx skip the cache.
    assert "proxy_ignore_headers Set-Cookie;" in conf
    assert "proxy_hide_header Set-Cookie;" in conf


def test_upstream_tls_is_verified_with_sni(conf):
    for line in ("proxy_ssl_server_name on;", "proxy_ssl_verify on;"):
        assert line in conf
    assert "proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;" in conf


NO_DOT_SEGMENTS = r"(?!.*\.\.)"
LOCATIONS = {
    8081: [
        r"~ ^/simple/" + NO_DOT_SEGMENTS + r"[A-Za-z0-9._-]+/?$",
        r"~ ^/files/" + NO_DOT_SEGMENTS + r"(packages/[A-Za-z0-9._/+-]+)$",
        "/",
    ],
    8082: [
        r"~ ^/"
        + NO_DOT_SEGMENTS
        + r"(@[A-Za-z0-9._~-]+/)?[A-Za-z0-9._~-]+(/-/[A-Za-z0-9._~-]+\.tgz)?$",
        "/",
    ],
    8083: [
        r"~ ^/"
        + NO_DOT_SEGMENTS
        + r"[A-Za-z0-9._~!/+-]+/@(v/[A-Za-z0-9._+!-]+\.(info|mod|zip)|v/list|latest)$",
        r"~ ^/sumdb/"
        + NO_DOT_SEGMENTS
        + r"(latest|lookup/[A-Za-z0-9._/@+=!-]+|tile/[0-9]+/[0-9a-z./]+)$",
        "/",
    ],
    8084: [
        "= /config.json",
        r"~ ^/(?:[12]|3/[a-z0-9_-]|[a-z0-9_-]{2}/[a-z0-9_-]{2})/[a-z0-9_-]+$",
        r"~ ^/dl/" + NO_DOT_SEGMENTS + r"([A-Za-z0-9_-]+)/([A-Za-z0-9.+-]+)/download$",
        "/",
    ],
}
ALLOWED = [
    "/simple/requests/",
    "/files/packages/ab/cd/requests-2.32.3-py3-none-any.whl",
    "/@types/node",
    "/left-pad/-/left-pad-1.3.0.tgz",
    "/github.com/pkg/errors/@v/v0.9.1.info",
    "/sumdb/lookup/github.com/pkg/errors@v0.9.1",
    "/se/rd/serde",
    "/dl/itoa/1.0.9/download",
]
REJECTED = [
    "/files/packages/../../simple/requests/",
    "/github.com/x/../../y/@v/list",
    "/sumdb/lookup/../../x",
    "/sumdb/tile/8/0/../x",
    "/files/packages/a\r\nHost: evil",
    "/-/v1/search",
    "/-/whoami",
    "/dl/../x/1.0.0/download",
]


def _location_lines(block: str) -> list[str]:
    """Each location's modifier and pattern, nginx quoting removed."""
    return [m.replace('"', "") for m in re.findall(r"^\s+location (.+?) \{", block, re.M)]


def test_every_location_pattern_is_pinned_exactly(conf):
    servers = _servers(conf)
    assert {port: _location_lines(block) for port, block in servers.items()} == LOCATIONS


def test_patterns_admit_real_paths_and_refuse_dot_segments_and_extras(conf):
    patterns = [
        re.compile(line[2:]) for lines in LOCATIONS.values() for line in lines if line[:2] == "~ "
    ]
    for path in ALLOWED:
        assert any(p.search(path) for p in patterns), path
    for path in REJECTED:
        assert not any(p.search(path) for p in patterns), path


def test_rewritten_links_point_at_the_mirror_itself(conf):
    servers = _servers(conf)
    assert f"'https://files.pythonhosted.org/' 'http://{MIRROR_IP}:8081/files/'" in servers[8081]
    assert f"'https://registry.npmjs.org/' 'http://{MIRROR_IP}:8082/'" in servers[8082]
    assert f'"dl":"http://{MIRROR_IP}:8084/dl"' in servers[8084]
    assert not re.search(r"https://(files\.pythonhosted|registry\.npmjs)[^ ]* 'https", conf)


def test_cache_is_bounded_and_keyed_per_port(conf, svc):
    match = re.search(r"proxy_cache_path /tmp/cache .*max_size=(\d+)m min_free=(\d+)m", conf)
    tmpfs = re.search(r"size=(\d+)g", svc["tmpfs"][0])
    assert match and tmpfs
    max_size, min_free = int(match.group(1)), int(match.group(2))
    # In-flight downloads share the tmpfs: leave room beyond max_size for them (min_free is the
    # largest artifact the runbook promises), and keep the container limit above the tmpfs.
    assert min_free >= 500 and max_size + min_free < int(tmpfs.group(1)) * 1024
    assert (
        int(svc["deploy"]["resources"]["limits"]["memory"].rstrip("M")) > int(tmpfs.group(1)) * 1024
    )
    assert "proxy_cache_key $server_port$uri;" in conf


def test_service_is_hardened_pinned_and_pinned_to_a_static_ip(svc):
    assert re.search(r"@sha256:[0-9a-f]{64}$", svc["image"])
    assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"]
    assert svc["security_opt"] == ["no-new-privileges:true"]
    limits = svc["deploy"]["resources"]["limits"]
    assert {"cpus", "memory", "pids"} <= set(limits)
    assert svc.get("user") not in ("0", "root", 0) and "privileged" not in svc
    assert svc["networks"]["a4-builder"]["ipv4_address"] == MIRROR_IP
    assert ipaddress.ip_address(MIRROR_IP) < ipaddress.ip_address("10.213.77.128")
    assert set(svc["networks"]) == {"a4-builder", "a4-upstream"}


def test_squid_egress_is_gone():
    stack = yaml.safe_load(COMPOSE.read_text())
    assert "egress-proxy" not in stack["services"]
    assert "10.213.77.11" not in COMPOSE.read_text()
    left = {p.name for p in CONF.parent.iterdir()}
    assert not {n for n in left if "squid" in n or "platform-dst" in n}


def test_runbook_documents_each_client_setting_by_mirror_ip():
    text = RUNBOOK.read_text()
    for needle in (
        f"PIP_INDEX_URL=http://{MIRROR_IP}:8081/simple/",
        f"npm_config_registry=http://{MIRROR_IP}:8082/",
        f"GOPROXY=http://{MIRROR_IP}:8083",
        f'GOSUMDB="sum.golang.org http://{MIRROR_IP}:8083/sumdb"',
        f"sparse+http://{MIRROR_IP}:8084/",
        f"NO_PROXY={MIRROR_IP}",
    ):
        assert needle in text, needle
    assert "## Package mirrors (build authors)" in text
    assert "## Accepted residual risk" not in text
    assert "500 MiB" in text and "Cached responses live 1 h" in text
