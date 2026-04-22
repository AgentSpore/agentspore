"""Resilience error hierarchy.

Classified so the circuit breaker can count provider-health failures
(``UpstreamError``) without penalising caller issues (``AuthError``).
Unclassified ``Exception`` bubbling out of a guarded call is treated as
upstream.
"""

from __future__ import annotations


class ResilienceError(Exception):
    """Base. Subclasses tell the breaker how to react."""

    error_code: str = "resilience_error"


class UpstreamError(ResilienceError):
    """External dependency is unhealthy (5xx, timeouts, network)."""

    error_code = "upstream_error"


class AuthError(ResilienceError):
    """Caller credentials are invalid (401/403). Not a provider health
    problem — pass through without incrementing the breaker."""

    error_code = "auth_error"


class CircuitOpenError(UpstreamError):
    """Breaker is open — call short-circuited to protect the upstream."""

    error_code = "circuit_open"
