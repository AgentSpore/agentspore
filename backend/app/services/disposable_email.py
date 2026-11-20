"""Detect disposable mail domains that a static blocklist misses: rotating providers by MX host."""

import asyncio
from contextlib import suppress

import dns.asyncresolver
import dns.exception
import dns.resolver
from disposable_email_domains import blocklist
from loguru import logger

DISPOSABLE = "disposable_email"
DNS_UNAVAILABLE = "dns_unavailable"
DNS_TIMEOUT_SECONDS = 3.0
MAX_CONCURRENT_LOOKUPS = 20

# Providers that rotate throw-away domains faster than the blocklist is updated.
EXTRA_DOMAINS = frozenset({"euploading.com", "mailinator.com", "mailinator.net", "mailinator2.com"})
DISPOSABLE_MX_SUFFIXES = ("mail.tm", "mailinator.com")


def is_listed_domain(domain: str) -> bool:
    """Match the domain, its IDNA form and every parent suffix against the lists."""
    domain = domain.strip().strip(".").lower()
    forms = {domain}
    with suppress(UnicodeError):
        forms.add(domain.encode("idna").decode("ascii"))
    for form in forms:
        labels = form.split(".")
        suffixes = (".".join(labels[i:]) for i in range(len(labels) - 1))
        if any(suffix in blocklist or suffix in EXTRA_DOMAINS for suffix in suffixes):
            return True
    return False


async def resolve_mx(domain: str) -> list[str]:
    """MX hosts of a domain; `[]` when DNS answers that there are none.

    Timeouts and resolver failures propagate as `dns.exception.DNSException`.
    """
    try:
        answer = await dns.asyncresolver.resolve(domain, "MX", lifetime=DNS_TIMEOUT_SECONDS)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    return [str(record.exchange).rstrip(".").lower() for record in answer]


def is_disposable_mx(host: str) -> bool:
    """True when the MX host is a known disposable provider or one of its subdomains."""
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in DISPOSABLE_MX_SUFFIXES)


async def mx_hold(domain: str) -> str | None:
    """Hold code for a domain by MX: disposable, unknown (DNS failed) or None when clean."""
    if is_listed_domain(domain):
        return DISPOSABLE
    try:
        hosts = await resolve_mx(domain)
    except (dns.exception.DNSException, OSError):
        logger.warning("MX lookup failed")
        return DNS_UNAVAILABLE
    return DISPOSABLE if any(is_disposable_mx(host) for host in hosts) else None


async def mx_holds(domains: set[str]) -> dict[str, str | None]:
    """`mx_hold` for each distinct domain, at most `MAX_CONCURRENT_LOOKUPS` at a time."""
    gate = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)

    async def limited(domain: str) -> str | None:
        async with gate:
            return await mx_hold(domain)

    ordered = sorted(domains)
    return dict(zip(ordered, await asyncio.gather(*map(limited, ordered)), strict=True))
