"""Mirror domains: map a request Host onto the configured allowlist.

The Host header is attacker-controlled, so it is used only to *pick* one of
``settings.domains``; an unmatched host yields ``None`` and callers fall back to
their configured default. ``X-Forwarded-Host`` is never read: Caddy forwards the
original Host unchanged and answers only for the listed domains.
"""

import re

from starlette.requests import Request

from app.core.config import get_settings

# Static documents (skill.md, ...) are written against this domain.
DOCS_DOMAIN = "agentspore.com"


def match_domain(host: str | None, domains: list[str]) -> str | None:
    """Return the allowlisted apex for ``host`` (apex or ``www.`` apex), else None."""
    name = (host or "").partition(":")[0].lower().rstrip(".")
    if name.startswith("www.") and name[4:] in domains:
        name = name[4:]
    return name if name in domains else None


def request_domain(request: Request, domains: list[str] | None = None) -> str | None:
    """Allowlisted domain the client used to reach us, or None."""
    return match_domain(request.headers.get("host"), domains or get_settings().domains)


def site_base_url(request: Request, domains: list[str] | None = None) -> str | None:
    """``https://<domain>`` for an allowlisted Host, else None (use configured default)."""
    domain = request_domain(request, domains)
    return f"https://{domain}" if domain else None


def localize_site_urls(text: str, request: Request, domains: list[str] | None = None) -> str:
    """Point absolute platform URLs in a static document at the domain being served."""
    domain = request_domain(request, domains)
    if not domain or domain == DOCS_DOMAIN:
        return text
    pattern = rf"(https?://(?:[a-z0-9-]+\.)*){re.escape(DOCS_DOMAIN)}\b"
    return re.sub(pattern, rf"\g<1>{domain}", text)
