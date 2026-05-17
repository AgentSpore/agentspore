"""SSRF protection: URL/domain validation for agent-runner tool calls."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

# Known redirect/paste domains used in prompt injection attacks
_BLOCKED_DOMAINS: frozenset[str] = frozenset([
    "gyo.tc", "bit.ly", "tinyurl.com", "t.co", "ow.ly", "rb.gy",
    "justpaste.it", "paste.ee", "pastebin.com", "rentry.co",
    "thecolony.cc", "skillmd.ai",
    "is.gd", "v.gd", "buff.ly", "short.io", "tiny.cc",
])

# Private/reserved IP ranges — SSRF protection
_PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def is_blocked_domain(url: str) -> bool:
    """True if URL points to a known redirect/paste domain."""
    try:
        host = urlparse(url).hostname or ""
        host = host.lower().lstrip("www.")
        return host in _BLOCKED_DOMAINS or any(host.endswith("." + d) for d in _BLOCKED_DOMAINS)
    except Exception:
        return False


def is_private_ip(url: str) -> bool:
    """True if URL hostname is a private/reserved IP literal (SSRF guard).

    Note: DNS is NOT resolved here — only literal IP addresses in the URL
    hostname are checked.
    """
    try:
        host = urlparse(url).hostname or ""
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except (ValueError, Exception):
        return False


def is_safe_url(url: str) -> bool:
    """True if URL is safe to fetch (not a blocked domain, not a private IP)."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    return not is_blocked_domain(url) and not is_private_ip(url)


def extract_urls(text: str) -> list[str]:
    """Extract all http/https URLs from text."""
    return re.findall(r'https?://[^\s\'"<>]+', text, re.IGNORECASE)


def get_blocked_urls(text: str) -> list[str]:
    """Return list of URLs in text that are blocked (redirect/paste domains or private IPs)."""
    return [url for url in extract_urls(text) if not is_safe_url(url)]
