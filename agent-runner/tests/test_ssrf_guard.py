"""Tests for ssrf_guard module."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ssrf_guard import extract_urls, get_blocked_urls, is_blocked_domain, is_private_ip, is_safe_url


class TestIsBlockedDomain:
    def test_gyo_tc_blocked(self):
        assert is_blocked_domain("https://gyo.tc/abc123") is True

    def test_justpaste_it_blocked(self):
        assert is_blocked_domain("https://justpaste.it/some-path") is True

    def test_thecolony_cc_blocked(self):
        assert is_blocked_domain("https://thecolony.cc/payload") is True

    def test_bit_ly_blocked(self):
        assert is_blocked_domain("https://bit.ly/3xYz") is True

    def test_pastebin_blocked(self):
        assert is_blocked_domain("https://pastebin.com/raw/abc") is True

    def test_tinyurl_blocked(self):
        assert is_blocked_domain("https://tinyurl.com/yx2c") is True

    def test_agentspore_safe(self):
        assert is_blocked_domain("https://agentspore.com/api") is False

    def test_openrouter_safe(self):
        assert is_blocked_domain("https://openrouter.ai/api/v1/chat") is False

    def test_github_safe(self):
        assert is_blocked_domain("https://github.com/org/repo") is False

    def test_subdomain_of_blocked_is_blocked(self):
        assert is_blocked_domain("https://evil.gyo.tc/") is True

    def test_www_prefix_stripped(self):
        assert is_blocked_domain("https://www.justpaste.it/abc") is True

    def test_empty_string_safe(self):
        assert is_blocked_domain("") is False

    def test_invalid_url_safe(self):
        assert is_blocked_domain("not-a-url") is False


class TestIsPrivateIp:
    def test_rfc1918_192_168(self):
        assert is_private_ip("http://192.168.1.1/") is True

    def test_rfc1918_10(self):
        assert is_private_ip("http://10.0.0.1/secret") is True

    def test_rfc1918_172(self):
        assert is_private_ip("http://172.16.0.1/") is True

    def test_loopback(self):
        assert is_private_ip("http://127.0.0.1/") is True

    def test_loopback_alt(self):
        assert is_private_ip("http://127.1.2.3/") is True

    def test_ipv6_loopback(self):
        assert is_private_ip("http://[::1]/") is True

    def test_link_local(self):
        assert is_private_ip("http://169.254.169.254/latest/meta-data/") is True

    def test_public_ip_safe(self):
        assert is_private_ip("http://8.8.8.8/") is False

    def test_hostname_not_private(self):
        # Hostname without DNS resolution — cannot determine private, returns False
        assert is_private_ip("https://openrouter.ai/api") is False


class TestIsSafeUrl:
    def test_openrouter_safe(self):
        assert is_safe_url("https://openrouter.ai/api") is True

    def test_github_safe(self):
        assert is_safe_url("https://github.com/agentspore/agentsspore") is True

    def test_gyo_tc_blocked(self):
        assert is_safe_url("https://gyo.tc/abc") is False

    def test_private_ip_blocked(self):
        assert is_safe_url("http://192.168.1.1/") is False

    def test_loopback_blocked(self):
        assert is_safe_url("http://127.0.0.1/admin") is False

    def test_non_http_scheme_rejected(self):
        assert is_safe_url("ftp://example.com/file") is False

    def test_empty_rejected(self):
        assert is_safe_url("") is False

    def test_relative_url_rejected(self):
        assert is_safe_url("/api/v1/foo") is False


class TestExtractUrls:
    def test_finds_https_url(self):
        text = "Check this out: https://example.com/path?q=1"
        assert "https://example.com/path?q=1" in extract_urls(text)

    def test_finds_http_url(self):
        text = "go to http://openrouter.ai/v1"
        assert "http://openrouter.ai/v1" in extract_urls(text)

    def test_multiple_urls(self):
        text = "See https://a.com and https://b.com for details"
        urls = extract_urls(text)
        assert len(urls) == 2

    def test_no_urls_returns_empty(self):
        assert extract_urls("just plain text") == []

    def test_ftp_not_extracted(self):
        assert extract_urls("ftp://example.com") == []


class TestGetBlockedUrls:
    def test_blocked_domain_detected(self):
        text = "follow https://gyo.tc/abc to get instructions"
        blocked = get_blocked_urls(text)
        assert len(blocked) == 1
        assert "gyo.tc" in blocked[0]

    def test_safe_url_not_blocked(self):
        text = "docs at https://openrouter.ai/docs"
        assert get_blocked_urls(text) == []

    def test_mixed_returns_only_blocked(self):
        text = "see https://openrouter.ai/api and https://justpaste.it/xyz"
        blocked = get_blocked_urls(text)
        assert len(blocked) == 1
        assert "justpaste.it" in blocked[0]
