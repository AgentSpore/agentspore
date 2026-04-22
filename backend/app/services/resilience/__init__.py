"""Resilience primitives (OSS-lite).

Circuit breaker + execution log — observability and failure protection
for outbound calls. EE extends the log with saga compensation and
per-integration breaker keying.
"""

from __future__ import annotations

from .circuit_breaker import CircuitBreaker, CircuitBreakerPolicy
from .errors import AuthError, CircuitOpenError, ResilienceError, UpstreamError
from .execution_log import ExecutionLogger, ExecutionStep, canonical_input_hash

__all__ = [
    "AuthError",
    "CircuitBreaker",
    "CircuitBreakerPolicy",
    "CircuitOpenError",
    "ExecutionLogger",
    "ExecutionStep",
    "ResilienceError",
    "UpstreamError",
    "canonical_input_hash",
]
