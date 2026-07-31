"""Rate limiting for the Healer ingest endpoint.

The generated app reports *every* unhandled exception to ``/healer/ingest``.
One broken route under production traffic emits thousands of identical errors a
minute, and each ticket that reaches tier 2 or 3 costs an LLM round trip. Left
unbounded, a single bug converts directly into unbounded spend and a queue
whose useful tickets are buried behind duplicates.

Limiting per ``contract_fqn`` — rather than per client IP — matches the shape
of the abuse: the fan-out comes from one failing contract, and there is exactly
one client. A global bucket sits behind the per-contract buckets so a fault
that sprays errors across many contracts is still bounded.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

INGEST_RATE_ENV = "SPECORA_HEALER_INGEST_RATE_PER_MINUTE"
INGEST_BURST_ENV = "SPECORA_HEALER_INGEST_BURST"
INGEST_GLOBAL_RATE_ENV = "SPECORA_HEALER_INGEST_GLOBAL_RATE_PER_MINUTE"

DEFAULT_RATE_PER_MINUTE = 12.0
DEFAULT_BURST = 6.0
DEFAULT_GLOBAL_RATE_PER_MINUTE = 60.0

UNATTRIBUTED_KEY = "<unattributed>"

# Buckets for contracts that stopped failing are dropped once they have been
# idle for longer than it takes any bucket to refill completely.
_IDLE_EVICTION_SECONDS = 900.0


@dataclass
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: float = 0.0
    scope: str = ""


class _Bucket:
    __slots__ = ("tokens", "capacity", "rate", "updated_at")

    def __init__(self, capacity: float, rate: float, now: float) -> None:
        self.tokens = capacity
        self.capacity = capacity
        self.rate = rate
        self.updated_at = now


class TokenBucketLimiter:
    """Token-bucket limiter keyed by contract FQN, with a global ceiling."""

    def __init__(
        self,
        rate_per_minute: float | None = None,
        burst: float | None = None,
        global_rate_per_minute: float | None = None,
    ) -> None:
        self.rate_per_second = (
            _positive_env(INGEST_RATE_ENV, DEFAULT_RATE_PER_MINUTE)
            if rate_per_minute is None
            else rate_per_minute
        ) / 60.0
        self.burst = (
            _positive_env(INGEST_BURST_ENV, DEFAULT_BURST) if burst is None else burst
        )
        self.global_rate_per_second = (
            _positive_env(INGEST_GLOBAL_RATE_ENV, DEFAULT_GLOBAL_RATE_PER_MINUTE)
            if global_rate_per_minute is None
            else global_rate_per_minute
        ) / 60.0
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}
        # The global bucket holds a minute of the global rate, independent of
        # the per-contract burst: one noisy contract must not exhaust the
        # capacity every other contract shares.
        self._global = _Bucket(
            max(1.0, self.global_rate_per_second * 60.0),
            self.global_rate_per_second,
            time.monotonic(),
        )

    def check(self, key: str | None) -> RateLimitDecision:
        """Consume one token for *key*. Does not consume when it denies."""
        scoped_key = key or UNATTRIBUTED_KEY
        now = time.monotonic()
        with self._lock:
            self._evict_idle(now)
            bucket = self._buckets.get(scoped_key)
            if bucket is None:
                bucket = _Bucket(self.burst, self.rate_per_second, now)
                self._buckets[scoped_key] = bucket

            per_key_wait = _refill_and_peek(bucket, now)
            if per_key_wait > 0:
                return RateLimitDecision(False, per_key_wait, scope=scoped_key)

            global_wait = _refill_and_peek(self._global, now)
            if global_wait > 0:
                return RateLimitDecision(False, global_wait, scope="global")

            bucket.tokens -= 1.0
            self._global.tokens -= 1.0
            return RateLimitDecision(True, scope=scoped_key)

    def _evict_idle(self, now: float) -> None:
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.updated_at > _IDLE_EVICTION_SECONDS
        ]
        for key in stale:
            del self._buckets[key]


def _refill_and_peek(bucket: _Bucket, now: float) -> float:
    """Refill *bucket* and return the seconds to wait, 0.0 if a token is available."""
    elapsed = max(0.0, now - bucket.updated_at)
    bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.rate)
    bucket.updated_at = now
    if bucket.tokens >= 1.0:
        return 0.0
    if bucket.rate <= 0:
        return float("inf")
    return (1.0 - bucket.tokens) / bucket.rate


def _positive_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default
