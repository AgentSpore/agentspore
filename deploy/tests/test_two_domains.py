"""Static contract of the two-domain mirror: Caddyfile, caddy image, compose, env docs."""

import re
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[1]
ROOT = DEPLOY.parent
DOMAINS = ("agentspore.com", "agentspore.ru")


@pytest.fixture(scope="module")
def caddyfile() -> str:
    return (DEPLOY / "Caddyfile").read_text()


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((DEPLOY / "docker-compose.prod.yml").read_text())


def _block(text: str, address: str) -> str:
    """Body of a top-level site block or snippet, matched by its brace depth."""
    start = re.search(rf"(?m)^{re.escape(address)} \{{\n", text)
    assert start, f"no block for {address}"
    depth, i = 1, start.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[start.end() : i - 1]


@pytest.mark.parametrize("domain", DOMAINS)
class TestCaddyfileSites:
    def test_apex_imports_the_shared_site_snippet(self, caddyfile, domain):
        assert "import agentspore_site" in _block(caddyfile, domain)

    def test_www_redirects_to_its_own_apex(self, caddyfile, domain):
        body = _block(caddyfile, f"www.{domain}")
        assert f"redir https://{domain}{{uri}} permanent" in body

    def test_wildcard_imports_the_services_snippet(self, caddyfile, domain):
        assert "import agentspore_services" in _block(caddyfile, f"*.{domain}")


class TestSharedSnippets:
    def test_rate_limits_live_in_the_shared_site_snippet(self, caddyfile):
        site = _block(caddyfile, "(agentspore_site)")
        assert site.count("rate_limit @") == 5
        for domain in DOMAINS:
            assert domain not in re.sub(r"wss://\S+", "", site)

    def test_csp_connect_src_lists_both_websocket_origins(self, caddyfile):
        csp = re.search(r'Content-Security-Policy "([^"]+)"', caddyfile).group(1)
        connect = re.search(r"connect-src ([^;]+)", csp).group(1).split()
        assert "'self'" in connect
        for domain in DOMAINS:
            assert f"wss://{domain}" in connect

    def test_service_hosts_use_cloudflare_dns_challenge_without_inline_token(self, caddyfile):
        body = _block(caddyfile, "(agentspore_services)")
        assert "dns cloudflare {env.CLOUDFLARE_API_TOKEN}" in body

    def test_service_hosts_go_to_the_backend_gateway_only(self, caddyfile):
        body = _block(caddyfile, "(agentspore_services)")
        assert re.findall(r"reverse_proxy (\S+)", body) == ["backend:8000"]
        assert "frontend" not in body


class TestImage:
    def test_cloudflare_module_pinned_and_ratelimit_invariant_kept(self):
        text = (DEPLOY / "Dockerfile.caddy").read_text()
        assert re.search(r"--with github\.com/caddy-dns/cloudflare@v\d+\.\d+\.\d+", text)
        assert "--with github.com/mholt/caddy-ratelimit" in text
        assert "INVARIANT(deploy)" in text


class TestCompose:
    def test_only_caddy_publishes_ports_and_only_80_443(self, compose):
        published = {n: s["ports"] for n, s in compose["services"].items() if s.get("ports")}
        assert set(published) == {"caddy"}
        assert {p.split(":")[1] for p in published["caddy"]} == {"80", "443", "443/udp"}

    def test_caddy_receives_the_cloudflare_token_from_env(self, compose):
        env = compose["services"]["caddy"]["environment"]
        assert env["CLOUDFLARE_API_TOKEN"].startswith("${CLOUDFLARE_API_TOKEN:?")

    def test_backend_gets_domains_list_and_one_canonical_oauth_host(self, compose):
        env = compose["services"]["backend"]["environment"]
        assert "agentspore.ru" in env["DOMAINS"]
        assert env["OAUTH_REDIRECT_BASE_URL"].startswith("https://${DOMAIN")
        assert env["GITHUB_OAUTH_REDIRECT_URI"].startswith("https://${DOMAIN")

    def test_browser_calls_are_same_origin_and_ssr_uses_the_internal_url(self, compose):
        front = compose["services"]["frontend"]
        assert front["build"]["args"]["NEXT_PUBLIC_API_URL"] == ""
        assert front["environment"]["INTERNAL_API_URL"] == "http://backend:8000"


def test_env_example_documents_new_names_without_values():
    lines = (ROOT / ".env.example").read_text().splitlines()
    for name in ("DOMAINS", "CLOUDFLARE_API_TOKEN"):
        assert [ln for ln in lines if ln.lstrip("# ").startswith(f"{name}=")], name
    token = next(ln for ln in lines if ln.lstrip("# ").startswith("CLOUDFLARE_API_TOKEN="))
    assert token.split("=", 1)[1].strip() == ""
